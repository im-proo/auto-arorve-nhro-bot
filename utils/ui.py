"""Rich message templates and keyboards.

Every screen the bot shows is built here. Handlers pass values in and hand the
returned HTML/keyboard to utils.rich; they never assemble markup themselves.

Style rules enforced by this module:
  * mono symbols only -- no coloured emoji in the bot's own UI
  * bold sans-serif titles, small caps labels (utils/textstyle)
  * <h2> for titles, <h3> for sections, <hr/> and blockquotes for structure
  * every button carries an explicit style: primary / success / danger
  * the back/cancel button is always danger
  * dynamic values are escaped and never restyled
"""

import html
import re
import time
from dataclasses import dataclass

from telebot import types

import config
from database import turso as db
from utils import helpers, pending, textstyle as ts

esc = html.escape

PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"

CHANNEL = "channel"


# ── Primitives ───────────────────────────────────────────────

def _cb(action, chat_id=None):
    """Callback payload, optionally carrying the chat it applies to."""
    return f"{action}:{chat_id}" if chat_id is not None else action


def _onoff(value):
    return "ON" if value else "OFF"


def _t(text):
    """Title style: bold sans-serif."""
    return ts.bold(text)


def _l(text):
    """Label style: small caps."""
    return ts.small_caps(text)


def _quote(lines):
    return "<blockquote>" + "<br/>".join(lines) + "</blockquote>"


def _brand_name():
    return config.BRAND.replace("◈", "").strip()


def _footer():
    return f"<p>◈ {_t(_brand_name())} · v{config.VERSION}</p>"


def _lines(pairs, width=26):
    """Dotted alignment block for status lists."""
    return [f"▸ {label} {'.' * max(3, width - len(label))} {value}" for label, value in pairs]


def _add_url(query):
    return f"https://t.me/{config.BOT_USERNAME}?{query}"


def is_channel(chat):
    return (chat or {}).get("chat_type") == CHANNEL


@dataclass(frozen=True)
class Button:
    """One styled inline button."""

    text: str
    action: str | None = None
    url: str | None = None
    style: str = PRIMARY
    copy_text: str | None = None

    def to_telegram(self):
        if self.copy_text:
            return types.InlineKeyboardButton(
                text=self.text,
                copy_text=types.CopyTextButton(text=self.copy_text),
                style=self.style,
            )
        if self.url:
            return types.InlineKeyboardButton(text=self.text, url=self.url, style=self.style)
        return types.InlineKeyboardButton(text=self.text, callback_data=self.action, style=self.style)


class Keyboard:
    """Rows of styled buttons, converted to InlineKeyboardMarkup on demand."""

    def __init__(self, *rows):
        self.rows = [list(row) for row in rows if row]

    def to_markup(self):
        return types.InlineKeyboardMarkup([[button.to_telegram() for button in row] for row in self.rows])


def _chunked(buttons, size=2):
    return [buttons[index:index + size] for index in range(0, len(buttons), size)]


# ── Media ────────────────────────────────────────────────────

def _age_value(settings):
    if not settings.get("account_age"):
        return "OFF"
    return f"ON · {int(settings.get('min_age_days') or 0)}d"


def _schedule_value(settings):
    if not settings.get("schedule_on"):
        return "OFF"
    start = int(settings.get("schedule_start") or 0)
    end = int(settings.get("schedule_end") or 0)
    return f"ON · {start:02d}-{end:02d}h"


def _slide_ids():
    ids = []
    for file_id in config.PROMO_IMAGES:
        if helpers.usable_file_id(file_id):
            ids.append(file_id)
    return ids[:3]


def _total_pending(chat):
    """Total visible requests for the settings preview, offline then cached."""
    approved = db.get_stats(chat["group_id"])
    cache = pending.get(chat["group_id"])
    return approved, (len(cache) if cache else 0)


MENU_VIDEO_ID = "menu_video"

# Media links live in the database so /admin can change them without a deploy.
_media_cache = {}
_MEDIA_TTL = 15


def _media_value(key, fallback):
    """A file_id or https link from bot_settings, cached for a few seconds."""
    cached = _media_cache.get(key)
    now = time.time()
    if cached is not None and now - cached[0] < _MEDIA_TTL:
        return cached[1]
    value = db.get_setting(key) or fallback
    _media_cache[key] = (now, value)
    return value


def menu_video_id():
    """Main menu clip: admin setting first, config.py as the fallback.

    A t.me post link is skipped here: the rich media block needs something the
    API can fetch, and a private post is not it.
    """
    value = _media_value("menu_video", config.MENU_VIDEO_FILE_ID)
    return None if helpers.is_post_link(value) else value


def verify_video_id():
    """Clip shown on the join + verify screen."""
    return _media_value("verify_video", config.VERIFY_VIDEO_FILE_ID)


def clear_media_cache():
    """Forget cached media links after an admin changes one."""
    _media_cache.clear()


def _slideshow_block():
    ids = _slide_ids()
    if not ids:
        return ""
    images = "".join(f'<img src="tg://photo?id=promo_{index}"/>' for index, _ in enumerate(ids))
    return f"<tg-slideshow>{images}<figcaption>◈ {_t(_brand_name())}</figcaption></tg-slideshow>"


def _media(ids, prefix):
    return [
        types.InputRichMessageMedia(
            id=f"{prefix}_{index}",
            media=types.InputMediaPhoto(media=file_id),
        )
        for index, file_id in enumerate(ids)
    ]


MENU_VIDEO_ID = "menu_video"


def _video_block():
    """The shared menu video, embedded as a real rich-message media block."""
    if not helpers.usable_file_id(menu_video_id()):
        return ""
    # Quoting the video directly gives it the blockquote frame; wrapping it in
    # <figure> first makes the API drop the media instead.
    return f'<blockquote><video src="tg://video?id={MENU_VIDEO_ID}"></video></blockquote>'


_VIDEO_BLOCK_RE = re.compile(
    r"<blockquote>\s*<video\b.*?</video>\s*</blockquote>"
    r"|<figure>\s*<video\b.*?</video>.*?</figure>"
    r"|<video\b.*?</video>",
    re.S | re.I,
)


def hoist_video(html):
    """Move the menu video above the text.

    Rich media must be its own block (nesting it in a blockquote makes the API
    drop it), so the clip leads the screen and the copy follows underneath.
    """
    match = _VIDEO_BLOCK_RE.search(html)
    if not match:
        return html
    return match.group(0) + html[:match.start()] + html[match.end():]


def menu_video_media():
    """Media attachment that resolves the tg://video link above."""
    video = menu_video_id()
    if not helpers.usable_file_id(video):
        return []
    return [
        types.InputRichMessageMedia(
            id=MENU_VIDEO_ID,
            media=types.InputMediaVideo(media=video),
        )
    ]


def main_menu_media():
    """Media list matching the slideshow inside main_menu_rich()."""
    return _media(_slide_ids(), "promo")


def owner_panel_media():
    """Media list matching the banner inside owner_panel_rich()."""
    banner = [config.OWNER_BANNER_FILE_ID] if helpers.usable_file_id(config.OWNER_BANNER_FILE_ID) else []
    return _media(banner, "owner_banner")


# ── Group screens ────────────────────────────────────────────

def main_menu_rich(group_name, approved_count, auto_approve, welcome_dm, show_stats=False, suspended=False):
    status = [(_l("Status"), "PAUSED" if suspended else "ACTIVE")]
    if show_stats:
        status.append((_l("Approved Members"), helpers.format_number(approved_count)))
    status.extend([
        (_l("Auto Approve"), _onoff(auto_approve)),
        (_l("Welcome DM"), _onoff(welcome_dm)),
    ])
    return "".join([
        f"<h2>◆ {_t('Main Menu')}</h2>",
        f"<h3>◇ {esc(group_name or 'Unnamed group')}</h3>",
        "<hr/>",
        _quote(_lines(status)),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def settings_rich(settings):
    # Per-chat preview of everything waiting to be admitted. The Bot API has
    # no pending-request list, so the total runs offline (already approved) and
    # the advance half lives in the bot's own cache.
    _TOGGLE_KEYS = {
        "accept_all": "accept_all",
        "skip_advanced": "skip_advanced",
    }
    _TOGGLE_LABELS = {
        "accept_all": "Accept All",
        "skip_advanced": "Skip Advanced",
    }
    approval = [
        (_l("Auto Approve"), _onoff(settings.get("auto_approve"))),
        (_l("Auto Decline"), _onoff(settings.get("auto_decline"))),
        (_l("Anti Raid"), _onoff(settings.get("anti_raid"))),
    ]
    filters = [
        (_l("Account Age"), _age_value(settings)),
        (_l("Photo Required"), _onoff(settings.get("photo_required"))),
        (_l("Username Required"), _onoff(settings.get("username_req"))),
    ]
    delivery = [
        (_l("Welcome DM"), _onoff(settings.get("welcome_dm"))),
        (_l("Log Channel"), "SET" if settings.get("log_channel") else "NOT SET"),
        (_l("Schedule"), _schedule_value(settings)),
    ]
    approved, hidden = _total_pending(settings)
    hidden_text = f" + {hidden} pending" if hidden else ""
    preview = [
        (_l("Total Requests"), f"{approved}{hidden_text}"),
        (_l("Auto Approve"), _onoff(settings.get("auto_approve"))),
        (_l("Auto Decline"), _onoff(settings.get("auto_decline"))),
        (_l("Anti Raid"), _onoff(settings.get("anti_raid"))),
    ]
    for key in _TOGGLE_KEYS:
        preview.append((_l(_TOGGLE_LABELS[key]), _onoff(settings.get(_TOGGLE_KEYS[key]))))
    return "".join([
        f"<h2>◆ {_t('Settings')}</h2>",
        f"<h3>◇ {esc(settings.get('group_name') or 'Unnamed group')}</h3>",
        "<hr/>",
        _quote(_lines(preview)),
        f"<h3>◇ {_t('Member Filters')}</h3>",
        _quote(_lines(filters)),
        f"<h3>◇ {_t('Delivery')}</h3>",
        _quote(_lines(delivery)),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def channel_settings_rich(chat):
    paused = bool(chat.get("suspended"))
    welcome = bool(chat.get("welcome_dm"))
    return "".join([
        f"<h2>◆ {_t('Channel Settings')}</h2>",
        f"<h3>◇ {esc(chat.get('group_name') or 'Unnamed channel')}</h3>",
        "<hr/>",
        _quote(_lines([
            (_l("Status"), "PAUSED" if paused else "ACTIVE"),
            (_l("Welcome DM"), _onoff(welcome)),
            (_l("Welcome Copy"), "CUSTOM" if chat.get("custom_welcome") else "DEFAULT"),
        ])),
        f"<h3>◇ {_t('Channel Features')}</h3>",
        _quote([
            f"‣ {_l('Welcome DM for everyone who opens your invite link')}",
            f"‣ {_l('Custom welcome copy written by you')}",
            f"‣ {_l('Use this channel as the approval log')}",
            f"‣ {_l('Pause or resume the bot any time')}",
        ]),
        f"<p>‣ {_l('Channels have no join requests, so approval filters stay group-only.')}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


_SETTING_BUTTONS = (
    ("Auto Approve", "auto_approve", "toggle_auto_approve"),
    ("Auto Decline", "auto_decline", "toggle_auto_decline"),
    ("Welcome DM", "welcome_dm", "toggle_welcome_dm"),
    ("Account Age", "account_age", "toggle_account_age"),
    ("Photo Req", "photo_required", "toggle_photo_req"),
    ("Username Req", "username_req", "toggle_username_req"),
    ("Schedule", "schedule_on", "toggle_schedule"),
    ("Anti Raid", "anti_raid", "toggle_anti_raid"),
)


def settings_keyboard(settings, back="main"):
    """Toggle grid for a group, or the channel panel when the target is one."""
    chat_id = settings.get("group_id")

    if is_channel(settings):
        paused = bool(settings.get("suspended"))
        welcome = bool(settings.get("welcome_dm"))
        return Keyboard(
            [
                Button(
                    f"{'◆' if welcome else '◇'} {_l('Welcome DM')}: {_onoff(welcome)}",
                    _cb("toggle_channel_welcome", chat_id),
                    style=SUCCESS if welcome else DANGER,
                ),
                Button(f"◇ {_l('Welcome Text')}", _cb("welcome_config", chat_id)),
            ],
            [
                Button(f"◆ {_l('Use for Logs')}", _cb("channel_use_logs", chat_id)),
                Button(f"◇ {_l('Copy Invite Link')}", copy_text=_channel_invite_url(chat_id), style=SUCCESS),
            ],
            [
                Button(
                    f"{'✗' if paused else '◆'} {_l('Pause Bot')}: {_onoff(paused)}",
                    _cb("toggle_channel_pause", chat_id),
                    style=SUCCESS if paused else DANGER,
                ),
                Button(f"← {_l('Chats')}", _cb("back_to_chats"), style=DANGER),
            ],
        )

    buttons = []
    for label, key, action in _SETTING_BUTTONS:
        enabled = bool(settings.get(key))
        buttons.append(Button(
            f"{'◆' if enabled else '◇'} {_l(label)}: {_onoff(enabled)}",
            _cb(action, chat_id),
            style=SUCCESS if enabled else DANGER,
        ))

    channel_set = bool(settings.get("log_channel"))
    buttons.append(Button(
        f"{'◆' if channel_set else '◇'} {_l('Log Channel')}: {'SET' if channel_set else 'NOT SET'}",
        _cb("toggle_log_channel", chat_id),
        style=SUCCESS if channel_set else DANGER,
    ))

    rows = _chunked(buttons, 2)
    back_button = (
        Button(f"← {_l('Chats')}", _cb("back_to_chats"), style=DANGER)
        if back == "chats"
        else Button(f"← {_l('Back')}", _cb("back_to_main", chat_id), style=DANGER)
    )
    rows.append([Button(f"◇ {_l('Advanced')}", _cb("advanced_settings", chat_id)), back_button])
    return Keyboard(*rows)


def advanced_settings_rich(settings):
    start = int(settings.get("schedule_start") or 0)
    end = int(settings.get("schedule_end") or 0)
    pairs = [
        (_l("Minimum Age"), f"{int(settings.get('min_age_days') or 0)}d"),
        (_l("Schedule Window"), f"{start:02d}:00 - {end:02d}:00 UTC"),
        (_l("Log Channel"), settings.get("log_channel") or "NOT SET"),
        (_l("Welcome Copy"), "CUSTOM" if settings.get("custom_welcome") else "DEFAULT"),
    ]
    return "".join([
        f"<h2>◆ {_t('Advanced')}</h2>",
        f"<h3>◇ {esc(settings.get('group_name') or 'Unnamed group')}</h3>",
        "<hr/>",
        _quote(_lines(pairs)),
        f"<p>‣ {_l('Bounds are UTC hours. Schedule and age filters apply only while enabled.')}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def advanced_settings_keyboard(settings):
    chat_id = settings.get("group_id")
    age = int(settings.get("min_age_days") or 0)
    start = int(settings.get("schedule_start") or 0)
    end = int(settings.get("schedule_end") or 0)
    return Keyboard(
        [
            Button(f"◆ {_l('Min Age')} {age}d -", _cb("age_minus", chat_id)),
            Button(f"◆ {_l('Min Age')} {age}d +", _cb("age_plus", chat_id)),
        ],
        [
            Button(f"◆ {_l('Start')} {start:02d}h -", _cb("sched_start_minus", chat_id)),
            Button(f"◆ {_l('Start')} {start:02d}h +", _cb("sched_start_plus", chat_id)),
        ],
        [
            Button(f"◆ {_l('End')} {end:02d}h -", _cb("sched_end_minus", chat_id)),
            Button(f"◆ {_l('End')} {end:02d}h +", _cb("sched_end_plus", chat_id)),
        ],
        [
            Button(f"◇ {_l('Welcome Message')}", _cb("welcome_config", chat_id)),
            Button(f"← {_l('Settings')}", _cb("settings_open", chat_id), style=DANGER),
        ],
    )


def stats_rich(group_name, approved, groups, users, global_approved=None, last_approved=None):
    last_seen = helpers.format_timestamp(last_approved) if last_approved else "N/A"
    global_total = approved if global_approved is None else global_approved
    return "".join([
        f"<h2>◆ {_t('Statistics')}</h2>",
        f"<h3>◇ {esc(group_name or 'Unnamed group')}</h3>",
        "<hr/>",
        _quote(_lines([
            (_l("Approved Here"), helpers.format_number(approved)),
            (_l("Last Approval"), last_seen),
        ])),
        f"<h3>◇ {_t('Global')}</h3>",
        _quote(_lines([
            (_l("Groups"), helpers.format_number(groups)),
            (_l("Users"), helpers.format_number(users)),
            (_l("Approved"), helpers.format_number(global_total)),
        ])),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def stats_keyboard(group_id=None):
    return Keyboard([Button(f"← {_l('Back')}", _cb("back_to_main", group_id), style=DANGER)])


def global_stats_rich(stats):
    return "".join([
        f"<h2>◆ {_t('Global Statistics')}</h2>",
        "<hr/>",
        _quote(_lines([
            (_l("Groups"), helpers.format_number(stats.get("groups", 0))),
            (_l("Users"), helpers.format_number(stats.get("users", 0))),
            (_l("Approved"), helpers.format_number(stats.get("approved", 0))),
        ])),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def help_rich(owner=False):
    member_commands = [
        ("/start", "Main menu"),
        ("/settings", "Pick a group or channel to manage"),
        ("/approveall", "Approve every pending request"),
        ("/declineall", "Decline every pending request"),
        ("/setwelcome", "Set a custom welcome message"),
        ("/resetwelcome", "Reset to the Nyro X welcome"),
        ("/suspend", "Suspend the bot in this group"),
        ("/resume", "Resume the bot in this group"),
        ("/cancel", "Cancel the current prompt"),
        ("/help", "This list"),
    ]
    owner_commands = [
        ("/stats [group_id]", "Approval statistics"),
        ("/owner", "Owner panel"),
        ("/broadcast", "Start a broadcast"),
        ("/bangroup", "Ban a group"),
        ("/banuser", "Ban a user"),
        ("/unbangroup", "Unban a group"),
        ("/unbanuser", "Unban a user"),
        ("/groups", "List active groups"),
        ("/globalstats", "Global statistics"),
    ]

    def block(commands):
        return _quote([f"‣ <code>{name}</code> — {_l(description)}" for name, description in commands])

    parts = [f"<h2>◆ {_t('Help')}</h2>", f"<h3>◇ {_t('Group Commands')}</h3>", block(member_commands)]
    if owner:
        parts += [f"<h3>◇ {_t('Owner Commands')}</h3>", block(owner_commands)]
    parts += [_video_block(), "<hr/>", _footer()]
    return "".join(parts)


# ── Chat picker ──────────────────────────────────────────────

def chat_chooser_rich(chats):
    if chats:
        lines = []
        for chat in chats:
            kind = "CHANNEL" if is_channel(chat) else "GROUP"
            lines.append(f"‣ {esc(chat.get('group_name') or chat.get('group_id'))} — {kind}")
    else:
        lines = [f"‣ {_l('No chats yet')}"]
    return "".join([
        f"<h2>◆ {_t('Choose a Chat')}</h2>",
        f"<p>‣ {_l('Select the group or channel you want to manage.')}</p>",
        "<hr/>",
        _quote(lines),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def chat_chooser_keyboard(chats):
    buttons = []
    for chat in chats[:20]:
        icon = "◇" if is_channel(chat) else "◆"
        name = helpers.truncate(str(chat.get("group_name") or chat.get("group_id")), 26)
        buttons.append(Button(f"{icon} {name}", _cb("settings_open", chat["group_id"])))

    rows = _chunked(buttons, 2) if buttons else []
    if not chats:
        rows.append([
            Button(f"◆ {_l('Add to Group')}", url=_add_url("startgroup=true"), style=SUCCESS),
            Button(f"◇ {_l('Add to Channel')}", url=_add_url("startchannel=true"), style=SUCCESS),
        ])
    rows.append([Button(f"← {_l('Home')}", _cb("home"), style=DANGER)])
    return Keyboard(*rows)


# ── Owner screens ────────────────────────────────────────────

def owner_panel_rich(total_groups, total_users, total_approved):
    banner = '<img src="tg://photo?id=owner_banner_0"/>' if helpers.usable_file_id(config.OWNER_BANNER_FILE_ID) else ""
    return "".join([
        banner,
        f"<h2>◈ {_t('Owner Panel')}</h2>",
        f"<p><b>{config.BOT_NAME}</b> · v{config.VERSION}</p>",
        "<hr/>",
        _quote(_lines([
            (_l("Groups"), helpers.format_number(total_groups)),
            (_l("Users"), helpers.format_number(total_users)),
            (_l("Approved"), helpers.format_number(total_approved)),
        ])),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def owner_keyboard():
    return Keyboard(
        [
            Button(f"◈ {_l('Force Join')}", _cb("force_join")),
            Button(f"◇ {_l('Media')}", _cb("media_config")),
        ],
        [
            Button(f"◆ {_l('Broadcast')}", _cb("broadcast_start")),
            Button(f"◇ {_l('Groups')}", _cb("groups_list")),
        ],
        [
            Button(f"◈ {_l('Ban Group')}", _cb("ban_group"), style=SUCCESS),
            Button(f"✗ {_l('Ban User')}", _cb("ban_user"), style=DANGER),
        ],
        [Button(f"◇ {_l('Maintenance Mode')}", _cb("maintenance_toggle"), style=DANGER)],
    )


def groups_list_rich(groups):
    if not groups:
        body = _quote([f"‣ {_l('No groups yet')}"])
    else:
        lines = []
        for group in groups[:30]:
            if group.get("suspended"):
                state = "SUSPENDED"
            elif group.get("auto_approve"):
                state = "AUTO"
            else:
                state = "MANUAL"
            kind = "CHANNEL" if is_channel(group) else "GROUP"
            lines.append(f"‣ {esc(group.get('group_name') or 'Unnamed')} — {group.get('group_id')} [{kind} · {state}]")
        if len(groups) > 30:
            lines.append(f"‣ …and {len(groups) - 30} more")
        body = _quote(lines)


def settings_list_rich(chats):
    """Letterhead above the /settings detail: every group + channel and a total."""
    if not chats:
        return f"<h2>◆ {_t('Settings')}</h2><p>‣ {_l('No chats yet')}</p>"
    lines = []
    for chat in chats:
        kind = "CHANNEL" if is_channel(chat) else "GROUP"
        lines.append(
            f"‣ {esc(chat.get('group_name') or 'Unnamed')} — {chat.get('group_id')} [{kind}]"
        )
    body = _quote(lines)
    return f"<h2>◆ {_t('Settings')}</h2><p>‣ {_l('Total')}: {helpers.format_number(len(chats))}</p>" + "<hr/>" + body


def back_to_owner_keyboard():
    return Keyboard([Button(f"← {_l('Owner Panel')}", _cb("owner_panel"), style=DANGER)])


def back_to_main_keyboard(group_id=None):
    return Keyboard([Button(f"← {_l('Back')}", _cb("back_to_main", group_id), style=DANGER)])


# ── Force join / verify screen ───────────────────────────────

def _channel_name(channel):
    return channel.get("title") or str(channel.get("channel_id"))


def verify_plain(channels, text=None):
    """Caption of the verify clip: plain text, safe for sendVideo."""
    body = (text or config.VERIFY_TEXT).strip()
    lines = [body, ""]
    for channel in channels:
        lines.append(f"‣ {_channel_name(channel)}")
    return helpers.truncate("\n".join(lines), 900)


def verify_rich(channels, text=None):
    """Verify screen when no clip can be sent: rich HTML instead of a caption."""
    body = (text or config.VERIFY_TEXT).strip()
    lines = [f"‣ {esc(_channel_name(channel))}" for channel in channels]
    missing_link = [channel for channel in channels if not channel.get("invite_link")]
    parts = [
        f"<h2>◆ {_t('Join & Verify')}</h2>",
        "<hr/>",
        _quote([esc(line) for line in body.splitlines() if line.strip()]),
        f"<h3>◇ {_t('Channels')}</h3>",
        _quote(lines or [f"‣ {_l('No channels required')}"]),
    ]
    if missing_link:
        parts.append(f"<p>‣ {_l('Channels without an invite link are listed above; ask an admin for one.')}</p>")
    parts += [_video_block(), "<hr/>", _footer()]
    return "".join(parts)


def verify_keyboard(channels):
    """One join button per channel, then the verify button."""
    rows = []
    for channel in channels:
        name = helpers.truncate(_channel_name(channel), 20)
        link = channel.get("invite_link")
        if not link:
            continue
        rows.append([Button(f"◆ {_l('Join')} · {name}", url=link, style=SUCCESS)])
    rows.append([Button(f"◆ {_l('Verify')}", _cb("verify_check"))])
    return Keyboard(*rows)


def force_join_rich(channels, verify_video=None):
    if channels:
        lines = [
            f"‣ {esc(_channel_name(channel))} — {channel.get('invite_link') or 'NO LINK'}"
            for channel in channels
        ]
    else:
        lines = [f"‣ {_l('No channels yet')}"]
    return "".join([
        f"<h2>◈ {_t('Force Join')}</h2>",
        f"<p>‣ {_l('Users must join every channel below before the bot answers them.')}</p>",
        "<hr/>",
        _quote(_lines([
            (_l("Required"), helpers.format_number(len(channels))),
            (_l("Verify Video"), "SET" if helpers.usable_file_id(verify_video) else "NOT SET"),
        ])),
        f"<h3>◇ {_t('Channels')}</h3>",
        _quote(lines),
        f"<p>‣ {_l('Add the bot as an admin in each channel with Invite Users so the link and the membership check work.')}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def force_join_keyboard(channels):
    rows = [[Button(f"◆ {_l('Add Channel')}", _cb("channel_add"), style=SUCCESS)]]
    for channel in channels:
        name = helpers.truncate(_channel_name(channel), 18)
        rows.append([Button(f"✗ {_l('Remove')} · {name}", _cb("channel_del", channel["channel_id"]), style=DANGER)])
    rows.append([
        Button(f"◇ {_l('Verify Video')}", _cb("verify_video_set")),
        Button(f"◇ {_l('Verify Text')}", _cb("verify_text_set")),
    ])
    rows.append([Button(f"← {_l('Admin')}", _cb("owner_panel"), style=DANGER)])
    return Keyboard(*rows)


def media_rich(menu_video, verify_video):
    return "".join([
        f"<h2>◈ {_t('Media')}</h2>",
        f"<p>‣ {_l('Change the clips by link, file id, or by sending the video to the bot.')}</p>",
        "<hr/>",
        _quote(_lines([
            (_l("Main Menu Video"), "SET" if helpers.usable_file_id(menu_video) else "NOT SET"),
            (_l("Verify Video"), "SET" if helpers.usable_file_id(verify_video) else "NOT SET"),
        ])),
        f"<p>‣ {_l('Accepted: https video links, Telegram file ids, or a forwarded clip.')}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def media_keyboard():
    return Keyboard(
        [
            Button(f"◆ {_l('Main Menu Video')}", _cb("menu_video_set")),
            Button(f"◇ {_l('Verify Video')}", _cb("verify_video_set")),
        ],
        [Button(f"◇ {_l('Reset Defaults')}", _cb("media_reset"), style=DANGER)],
        [Button(f"← {_l('Admin')}", _cb("owner_panel"), style=DANGER)],
    )


# ── Broadcast screens ────────────────────────────────────────

def broadcast_menu_rich():
    return "".join([
        f"<h2>◆ {_t('Broadcast')}</h2>",
        "<hr/>",
        _quote([
            f"‣ {_l('Target')} .............. {_l('Users DM Only')}",
            f"‣ {_l('Types')} ............... {_l('Text / Photo / Video / Rich Card')}",
        ]),
        f"<p>‣ {_l('Pick a type, then send the content when asked.')}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def broadcast_keyboard():
    return Keyboard(
        [Button(f"◇ {_l('Users DM Only')}", _cb("broadcast_target_users"))],
        [
            Button(f"◆ {_l('Text')}", _cb("broadcast_type_text")),
            Button(f"◇ {_l('Photo')}", _cb("broadcast_type_photo")),
        ],
        [
            Button(f"◈ {_l('Video')}", _cb("broadcast_type_video"), style=SUCCESS),
            Button(f"◇ {_l('Rich Card')}", _cb("broadcast_type_rich")),
        ],
        [
            Button(f"✗ {_l('Cancel')}", _cb("broadcast_cancel"), style=DANGER),
            Button(f"→ {_l('Preview')}", _cb("broadcast_preview"), style=SUCCESS),
        ],
    )


def broadcast_prompt_rich(kind):
    hints = {
        "text": "Send the message text now.",
        "photo": "Send the photo now. A caption is optional.",
        "video": "Send the video now. A caption is optional.",
        "rich": "Send the rich card HTML now. Tags such as <b>, <blockquote> and <h2> are supported.",
    }
    return "".join([
        f"<h2>◆ {_t('Broadcast Content')}</h2>",
        "<hr/>",
        _quote([
            f"‣ {_l('Type')} .............. {esc(kind.upper())}",
            f"‣ {_l('Target')} ............ {_l('Users DM Only')}",
        ]),
        f"<p>‣ {hints.get(kind, 'Send the content now.')}</p>",
        "<hr/>",
        _footer(),
    ])


def broadcast_cancel_keyboard():
    return Keyboard([Button(f"✗ {_l('Cancel')}", _cb("broadcast_cancel"), style=DANGER)])


def broadcast_report_rich(sent, failed, seconds):
    return "".join([
        f"<h2>◆ {_t('Broadcast Complete')}</h2>",
        "<hr/>",
        _quote(_lines([
            (_l("Sent"), helpers.format_number(sent)),
            (_l("Failed"), helpers.format_number(failed)),
            (_l("Time"), f"{seconds}s"),
        ])),
        "<hr/>",
        _footer(),
    ])


def welcome_config_rich(current_welcome, channel=False):
    default = config.CHANNEL_WELCOME if channel else config.DEFAULT_WELCOME
    body = current_welcome if current_welcome else default
    rendered = esc(body.strip()).replace("\n", "<br/>")
    placeholders = "{first_name} · {channel_name}" if channel else "{first_name} · {group_name}"
    return "".join([
        f"<h2>◆ {_t('Welcome Message')}</h2>",
        f"<h3>◇ {_t('Custom' if current_welcome else 'Default')} · {_l('Channel' if channel else 'Group')}</h3>",
        "<hr/>",
        f"<blockquote>{rendered}</blockquote>",
        f"<p>‣ {_l('Placeholders')}: {placeholders}</p>",
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def welcome_config_keyboard(group_id=None, back="advanced"):
    if back == "channel":
        back_button = Button(f"← {_l('Channel')}", _cb("settings_open", group_id), style=DANGER)
    else:
        back_button = Button(f"← {_l('Back')}", _cb("advanced_settings", group_id), style=DANGER)
    return Keyboard(
        [
            Button(f"◇ {_l('Set Custom')}", _cb("welcome_set_custom", group_id)),
            Button(f"◈ {_l('Reset Default')}", _cb("welcome_reset", group_id), style=DANGER),
        ],
        [back_button],
    )


# ── Shared / generic screens ─────────────────────────────────

def confirmation_rich(message, count):
    return "".join([
        f"<h2>◆ {_t('Confirm')}</h2>",
        "<hr/>",
        _quote([esc(line) for line in str(message).splitlines()]),
        f"<p>‣ {_l('Send to')} {helpers.format_number(count)} {_l('users? This cannot be undone.')}</p>",
        "<hr/>",
        _footer(),
    ])


def confirmation_keyboard():
    return Keyboard(
        [
            Button(f"✗ {_l('Cancel')}", _cb("broadcast_cancel"), style=DANGER),
            Button(f"◆ {_l('Confirm')}", _cb("broadcast_confirm"), style=SUCCESS),
        ],
    )


def prompt_rich(title, message, hint=None):
    parts = [f"<h2>◆ {_t(title)}</h2>", "<hr/>", f"<p>‣ {esc(message)}</p>"]
    if hint:
        parts.append(_quote([esc(line) for line in hint.splitlines()]))
    parts += ["<hr/>", _footer()]
    return "".join(parts)


def log_channel_prompt_rich(group):
    return prompt_rich(
        "Log Channel",
        "Send the channel @username or -100… id.",
        "Add me to that channel as an admin so I can post\napproval logs there.",
    )


def log_channel_prompt_keyboard(group_id=None):
    return Keyboard(
        [Button(f"✗ {_l('Remove Channel')}", _cb("log_channel_clear", group_id), style=DANGER)],
        [Button(f"← {_l('Back')}", _cb("advanced_settings", group_id), style=DANGER)],
    )


def back_to_settings_keyboard(group_id=None):
    return Keyboard([Button(f"← {_l('Settings')}", _cb("settings_open", group_id), style=DANGER)])


def back_to_advanced_keyboard(group_id=None):
    return Keyboard([Button(f"← {_l('Advanced')}", _cb("advanced_settings", group_id), style=DANGER)])


def back_to_chats_keyboard():
    return Keyboard([Button(f"← {_l('Chats')}", _cb("back_to_chats"), style=DANGER)])


def welcome_dm_rich(first_name, group_name, custom_welcome=None):
    """The rich DM an approved member receives.

    A custom welcome is the group admin's own text: placeholders are filled in
    and coloured emoji or rich HTML are passed through untouched.
    """
    if custom_welcome:
        body = _format_custom(custom_welcome, first_name=first_name, group_name=group_name).replace("\n", "<br/>")
        return f"{body}<hr/>{_footer()}"

    text = _format_custom(config.DEFAULT_WELCOME, first_name=first_name, group_name=group_name)
    return _render_welcome(text)


def channel_welcome_dm_rich(first_name, channel_name, custom_welcome=None):
    """The rich DM sent when someone opens the bot from a channel invite link."""
    if custom_welcome:
        body = _format_custom(
            custom_welcome,
            first_name=first_name,
            group_name=channel_name,
            channel_name=channel_name,
        ).replace("\n", "<br/>")
        return f"{body}<hr/>{_footer()}"

    text = _format_custom(
        config.CHANNEL_WELCOME,
        first_name=first_name,
        group_name=channel_name,
        channel_name=channel_name,
    )
    return _render_welcome(text)


def _render_welcome(text):
    """Render a welcome template as rich HTML.

    Blank-line separated blocks map onto the template's structure: heading,
    quotation, bullet lines, then the closing brand line. Only the heading and
    the brand line are restyled, so member and group names stay untouched.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
    if not blocks:
        return f"<h2>◆ {_t('Welcome')}</h2><hr/>{_footer()}"

    parts = []
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if index == 0:
            parts.append(f"<h2>◆ {_t(' '.join(lines).lstrip('◆ ').strip())}</h2>")
        elif index == len(blocks) - 1:
            parts.append(f"<hr/><p>{_t(lines[0])}</p>")
        elif all(line.startswith("‣") for line in lines):
            parts.extend(f"<p>{esc(line)}</p>" for line in lines)
        else:
            parts.append(f"<blockquote>{'<br/>'.join(esc(line) for line in lines)}</blockquote>")
    return "".join(parts)


def _format_custom(template, **values):
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        # Stray braces in the admin's copy must never break the welcome DM.
        return template


def channel_welcome_keyboard(username=None):
    if not username:
        return None
    return Keyboard([Button(f"◇ {_l('Open Channel')}", url=f"https://t.me/{username}", style=SUCCESS)])


def _channel_invite_url(chat_id):
    return _add_url(f"start=ch_{chat_id}")


def private_welcome_rich(first_name):
    return "".join([
        f"<h2>◆ {_t(config.BOT_NAME)}</h2>",
        f"<p>‣ {_l('Hello')}, {esc(first_name or 'there')}.</p>",
        _quote([
            f"‣ {_l('Add me to a group or channel to manage it')}",
            f"‣ {_l('Open /settings to pick the chat')}",
            f"‣ {_l('Channel invites can send a welcome DM')}",
            f"‣ {_l('Use /help for every command')}",
        ]),
        _video_block(),
        "<hr/>",
        _footer(),
    ])


def private_welcome_keyboard():
    return Keyboard(
        [
            Button(f"◆ {_l('Settings')}", _cb("open_chat_picker")),
            Button(f"◇ {_l('Help')}", _cb("help"), style=SUCCESS),
        ],
        [
            Button(f"◆ {_l('Add to Group')}", url=_add_url("startgroup=true"), style=SUCCESS),
            Button(f"◇ {_l('Add to Channel')}", url=_add_url("startchannel=true"), style=SUCCESS),
        ],
    )


def error_rich(message):
    return "".join([
        f"<h2>✗ {_t('Error')}</h2>",
        "<hr/>",
        _quote([esc(line) for line in str(message).splitlines()]),
        "<hr/>",
        _footer(),
    ])


def success_rich(message):
    return "".join([
        f"<h2>◆ {_t('Done')}</h2>",
        "<hr/>",
        _quote([esc(line) for line in str(message).splitlines()]),
        "<hr/>",
        _footer(),
    ])