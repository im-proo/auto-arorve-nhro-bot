"""Central configuration for Nyro X Auto Approve Bot.

Only the three secrets below are read from the environment. Everything else --
branding, artwork, copy and tuning limits -- lives here so that no handler or
module ever carries configuration of its own.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ── Secrets (environment only) ───────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]
# Optional so the bot also runs against a local SQLite file (file:bot.db);
# Turso reaches this point only when a token is actually present.
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

# ── Identity ─────────────────────────────────────────────────
# Replace with your own numeric Telegram user id (get it from @userinfobot).
OWNER_ID = 8900061448
# Replace with the bot's username, without the leading "@".
BOT_USERNAME = "Auto_request_accepterr_bot"

BOT_NAME = "Nyro X Auto Approve"
BRAND = "◈ Nyro X Bots"
VERSION = "1.0.0"

# ── Artwork ──────────────────────────────────────────────────
# Telegram file_id values. The "YOUR_FILE_ID" placeholders are skipped, so the
# bot renders fine with no artwork until real file ids are pasted in here.
PROMO_IMAGES = [
    "YOUR_FILE_ID_1",
    "YOUR_FILE_ID_2",
    "YOUR_FILE_ID_3",
]
OWNER_BANNER_FILE_ID = "YOUR_FILE_ID"

# Menu video shown on every panel (main menu, settings, advanced, features).
# This file_id belongs to this bot: it was minted by uploading the clip with
# BOT_TOKEN, so it can be reused indefinitely.
MENU_VIDEO_FILE_ID = (
    "BAACAgUAAyEFAAMBASIbQQADBGrwDQHMWrWkOw_hJPX6zeuG9ygYAAKTIgACnIyBV-BVO243gVKKPQQ"
)

# ── Copy ─────────────────────────────────────────────────────
DEFAULT_WELCOME = """
◆ Welcome, {first_name}

You have been approved to join
{group_name}

‣ Read the rules before chatting
‣ Stay respectful
‣ Have fun

◈ Nyro X Bots
"""

# ── Force join / verify screen ───────────────────────────────
# Fallback clip for the join+verify screen. Admins can replace it from /admin
# with a file_id, a direct https video link, or by forwarding the clip to the
# bot. A private post link (t.me/c/...) is not downloadable by the Bot API, so
# forward the clip to the bot once and the file_id is captured instead.
VERIFY_VIDEO_FILE_ID = MENU_VIDEO_FILE_ID

VERIFY_TEXT = """
◆ Join to continue

This bot works only for members of our channels.

‣ Join every channel below
‣ Tap Verify once you are in

◈ Nyro X Bots
"""

CHANNEL_WELCOME = """
◆ Welcome, {first_name}

You are now connected to
{channel_name}

‣ New drops land in the channel
‣ Turn notifications on
‣ Stay tuned

◈ Nyro X Bots
"""

# ── Runtime ──────────────────────────────────────────────────
# Render injects PORT; 8080 is the documented fallback for Docker/local runs.
HEALTH_PORT = int(os.environ.get("PORT", "8080"))
LOG_LEVEL = "INFO"

# Anti-raid: more approvals than this inside the window means the group is
# being flooded and the next requests are left pending instead of approved.
ANTI_RAID_WINDOW = 60          # seconds
ANTI_RAID_MAX_APPROVALS = 10   # approvals per window per group

# Broadcast pacing, in seconds, between two recipients.
BROADCAST_DELAY = 0.05
