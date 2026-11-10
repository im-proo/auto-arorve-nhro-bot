# ◈ Nyro X Auto Approve

Professional Telegram auto-approval bot for the **Nyro X Bots** brand. It approves
join requests, DMs approved members a rich welcome, and gives admins a rich
settings panel — all built on Bot API 10.3 rich messages with styled buttons.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Bot library | pyTelegramBotAPI `>= 4.37.0` (rich message support) |
| Database | Turso over HTTPS (built-in client), or local SQLite for development |
| Bot API | 10.3 — `sendRichMessage`, styled inline buttons |
| Hosting | Render (Docker web service) + Uptime Robot |

## Layout

```
main.py                 entry point: polling + Flask health endpoint
config.py               branding, limits, copy, artwork (only 3 env secrets)
database/turso.py       schema + every query, Turso HTTPS / SQLite backend
utils/
  ui.py                 every rich template and styled keyboard
  rich.py               rich send/edit with plain-HTML fallback
  helpers.py            permissions, formatting, callback plumbing
  state.py              short-lived flow state (prompts, confirmations)
  pending.py            cache of seen join requests for /approveall
  gate.py               force-join middleware: stops non-members early
handlers/
  start.py              /start, /help, panel routing, channel invite welcome
  settings.py           group toggles, channel panel, log channel, welcome copy
  membership.py         records every group and channel the bot is added to
  stats.py              /stats and the stats screen
  approve.py            join requests, /approveall, /declineall
  owner.py              /owner, /admin, bans, maintenance mode
  forcejoin.py          required channels, verify screen, media links
  broadcast.py          broadcast flow with preview and delivery report
  menus.py              shared screen senders
Dockerfile              Render image
```

## Setup

### 1. Bot

1. Create the bot with [@BotFather](https://t.me/BotFather), keep the token.
2. Turn on **join requests** for the group: *Group Settings → Members →
   Approve New Members* (or use an invite link with "Request Admin Approval").
3. Add the bot to the group **as an admin** with *Invite Users* and *Delete
   Messages* rights.

### 2. Turso

```bash
turso db create nyro-x
turso db show nyro-x --url          # TURSO_DATABASE_URL
turso db tokens create nyro-x       # TURSO_AUTH_TOKEN
```

Copy the URL exactly as the dashboard shows it. `libsql://`, `wss://` and
`https://` are all accepted and normalised to the HTTPS API, so any of them
works as `TURSO_DATABASE_URL`. Tables and indexes are created automatically on
first start.

**Local development:** set `TURSO_DATABASE_URL=file:bot.db` and leave
`TURSO_AUTH_TOKEN` empty. Anything that is not an `http(s)://`/`libsql://` URL
is opened as a plain SQLite file with the standard library, so the bot runs
with no Turso account at all. Copy that file up when you are ready to keep the
settings you made while testing.

### 3. Fill in the placeholders in `config.py`

These are intentionally hardcoded (not environment variables):

| Constant | What to put there |
|---|---|
| `OWNER_ID` | your numeric Telegram id (from [@userinfobot](https://t.me/userinfobot)) |
| `BOT_USERNAME` | the bot's username, without `@` |
| `PROMO_IMAGES` | 2–3 Telegram `file_id`s for the menu slideshow |
| `OWNER_BANNER_FILE_ID` | one `file_id` for the owner panel banner |
| `MENU_VIDEO_FILE_ID` | one video `file_id` shown on every panel (main menu, settings, advanced, stats, help) |

`YOUR_FILE_ID…` placeholders are detected and skipped, so the bot runs fine
before you add artwork. To get a `file_id`, send the image to the bot and read
it from the update, or forward it to [@JsonDumpBot](https://t.me/JsonDumpBot).

### 4. Environment

Only three values come from the environment:

```bash
export BOT_TOKEN="PASTE_YOUR_BOT_TOKEN_HERE"
export TURSO_DATABASE_URL="https://nyro-x.turso.io"
export TURSO_AUTH_TOKEN="PASTE_YOUR_TURSO_TOKEN_HERE"
```

For local work copy `.env.example` to `.env` — `python-dotenv` loads it.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Health check: <http://localhost:8080/> → `◆ Nyro X Auto Approve — Online`.

### Copying local test data to Turso

`sync_db.py` upserts every row of a local SQLite file into the database the
environment points at, so videos, verify text, required channels and groups you
configured while testing survive the switch:

```bash
export TURSO_DATABASE_URL="https://nyro-x.turso.io"
export TURSO_AUTH_TOKEN="..."
python sync_db.py            # reads ./sync.db by default, SYNC_SOURCE to change
```

It is safe to run twice: rows are matched on their primary key.

## Deploy on Render

1. **New → Web Service**, connect the repo, runtime **Docker**.
2. Add `BOT_TOKEN`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` under *Environment*.
3. Deploy. Render sets `PORT`; `config.HEALTH_PORT` honours it and falls back to
   8080, which is also what the Dockerfile exposes.
4. **Uptime Robot**: monitor type *HTTP(s)*, URL = your Render URL, interval
   5 minutes. The ping keeps the free instance awake and gives you downtime
   alerts.

## Commands

**Group admins:** `/start` `/settings` `/approveall` `/declineall`
`/setwelcome` `/resetwelcome` `/suspend` `/resume` `/cancel` `/help`

**Owner only:** `/stats` `/owner` `/admin` `/broadcast` `/bangroup` `/banuser`
`/unbangroup` `/unbanuser` `/groups` `/globalstats`

## Force join & verify

From `/admin` (same panel as `/owner`):

| Button | What it does |
|---|---|
| **Force Join** | Lists every required channel, with **Add Channel** and one remove button per channel |
| **Add Channel** | Send the channel `@username` or `-100…` id; the bot reads the title and creates the invite link itself |
| **Verify Video** | Send or forward a clip, or paste a `file_id` / direct `https` video link |
| **Verify Text** | The copy shown above the Join and Verify buttons |
| **Media** | Change the main menu clip and the verify clip, or reset both to `config.py` defaults |

Until a user has joined every channel in the list, the bot answers only with the
video, one **Join** button per channel and a **Verify** button. Group membership
is not enough, and the group command is not executed. Join requests are held
(not declined): once the member joins and taps **Verify**, the pending request
is approved automatically and the welcome DM is sent.

The owner is always exempt, so a misconfigured channel cannot lock you out of
the panel. If Telegram refuses the membership check (the bot was removed from a
channel), the gate lets that user through rather than locking everyone out.

**Video links:** add the bot to each required channel **as an administrator with
*Invite Users*** — the invite link is created for you and locked membership
checks need that right. The verify clip accepts any of:

| Value | How it is used |
|---|---|
| Forwarded video / `file_id` | sent with `sendVideo`, stored permanently |
| Direct `https` `.mp4` link | Telegram fetches it server-side |
| `https://t.me/c/4313979713/2/21` | the post is copied, so the bot must be a member of that channel |

If a post link cannot be copied (bot removed from the channel, post deleted),
the screen falls back to the default `config.py` clip, then to text.

## Approval pipeline

For every `chat_join_request`:

1. Hard blocks — banned group, banned user, suspended bot.
2. Filters, each only while enabled — schedule window (UTC), minimum account
   age, profile photo, username, anti-raid rate limit.
3. Approve, log the approval, register the user.
4. Rich welcome DM when `welcome_dm` is on.
5. Plain-text entry in the log channel when one is configured.

By default a request that fails a filter is **left pending**, so an admin can
still admit the member manually. Switch on **Auto Decline** in settings to
decline failed filter checks instead; bans, suspension and auto-approve-off are
never declined automatically.

## Channels

Channels have no join requests, so approval filters stay group-only. The
channel panel still offers:

| Feature | How it works |
|---|---|
| Welcome DM | **Copy Invite Link** copies `t.me/<bot>?start=ch_<id>`; opening it sends that channel's rich welcome DM |
| Welcome copy | same editor as groups, with `{first_name}` and `{channel_name}` |
| Log destination | **Use for Logs** points every group you administer at this channel |
| Pause / resume | stops the bot reacting while keeping every setting |

Channels are discovered from `my_chat_member` updates, so add or promote the bot
after deploying and they appear in `/settings`.

## Notes and limits

- **Rich messages degrade gracefully.** `utils/rich.py` sends
  `sendRichMessage` first; if the API refuses the payload it re-renders the same
  screen as plain HTML so no screen is lost.
- **`/approveall` works from a cache.** The Bot API delivers join requests only
  as `chat_join_request` updates — there is no method to list them. Requests
  seen since startup are cached in memory, so the cache starts empty after a
  restart. That is a platform limit, not a bug.
- **Account age is an estimate.** Telegram does not expose creation dates; the
  helper derives one from the user id, which is coarse. Never treat it as a
  security control.
- **Default UI stays mono.** Templates use ◆ ◇ ▸ ‣ ◈ ✗ ✓ only. Custom welcome
  messages typed by admins may use any emoji or rich HTML.
- **Prompts can be abandoned.** `/cancel` clears a half-finished prompt (log
  channel, welcome copy, broadcast) without touching saved settings.
- **Broadcast** targets users who started the bot (`started_bot = 1`) and are
  not banned; blocked users count as failures. Every run is written to
  `broadcast_log`.
