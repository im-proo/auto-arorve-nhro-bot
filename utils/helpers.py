"""Shared helpers: permissions, formatting and callback plumbing."""

import time
from datetime import datetime, timezone

import config

# Telegram allocated pre-2015 ids through a small counter; anything below this
# belongs to an older, long-standing account.
_SMALL_ID_CEILING = 2 ** 32
# Coarse origin used by the id -> creation-time approximation.
_ID_EPOCH = 1420070400  # 2015-01-01 00:00:00 UTC
_ASSUMED_OLD_ACCOUNT_DAYS = 3650


def is_owner(user_id):
    """True when the user is the bot owner configured in config.py."""
    return bool(config.OWNER_ID) and user_id == config.OWNER_ID


def is_admin(bot, user_id, chat_id):
    """True when the user administers the chat (the owner always counts)."""
    if is_owner(user_id):
        return True
    try:
        member = bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return getattr(member, "status", "") in ("administrator", "creator")


def get_account_age_days(user_id):
    """Approximate account age in days.

    Telegram hands out user ids roughly in sequence, and for 64-bit ids the
    upper 32 bits behave like a coarse creation timestamp. That heuristic is
    all this function offers, so the value is used as a soft filter only and
    never for security decisions. Very small ids predate the scheme and are
    treated as long-standing accounts.
    """
    if not user_id or user_id <= 0:
        return 0
    if user_id < _SMALL_ID_CEILING:
        return _ASSUMED_OLD_ACCOUNT_DAYS

    created = _ID_EPOCH + (user_id >> 32)
    age_seconds = time.time() - created
    if age_seconds <= 0:
        return 0
    return int(age_seconds // 86400)


def current_hour():
    """Current UTC hour as an integer (0-23)."""
    return datetime.now(timezone.utc).hour


def is_within_schedule(start_hour, end_hour):
    """True when the current UTC hour falls inside the window.

    Windows that cross midnight (for example 21 -> 6) are supported. A window
    whose bounds are equal means "all day".
    """
    start_hour = int(start_hour) % 24
    end_hour = int(end_hour) % 24
    if start_hour == end_hour:
        return True
    hour = current_hour()
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def format_number(value):
    """Compact number formatting: 1000 -> 1K, 1500 -> 1.5K, 1000000 -> 1M."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "0"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if number >= limit:
            scaled = number / limit
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    return f"{sign}{number}"


def format_timestamp(timestamp=None):
    """UTC timestamp used in log lines, e.g. 2026-11-07 09:41 UTC."""
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def truncate(text, limit):
    """Hard-truncate a string so a message never exceeds a Bot API limit."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def usable_file_id(file_id):
    """False for the YOUR_FILE_ID placeholders in config.py."""
    return bool(file_id) and not str(file_id).startswith("YOUR_FILE_ID")


def parse_post_link(value):
    """(source_chat, message_id) for a Telegram post link, else None.

    Handles the private form `t.me/c/<internal_id>/<message>` (and the forum
    topic variant with one extra segment) plus public `t.me/<username>/<id>`.
    A copied post is the only way to reuse a clip that lives in a private
    channel: the Bot API cannot download those links itself.
    """
    text = str(value or "").strip()
    text = text.split("?")[0]
    if text.startswith("https://"):
        text = text[len("https://"):]
    elif text.startswith("http://"):
        text = text[len("http://"):]
    if not text.startswith("t.me/"):
        return None

    parts = [part for part in text[len("t.me/"):].split("/") if part]
    if len(parts) < 2:
        return None
    try:
        message_id = int(parts[-1])
    except ValueError:
        return None

    if parts[0] == "c":
        if len(parts) < 3:
            return None
        try:
            return (int("-100" + parts[1]), message_id)
        except ValueError:
            return None

    if len(parts) == 2 and not parts[0].startswith("+"):
        return (parts[0], message_id)
    return None


def is_post_link(value):
    """True for a t.me post link (which cannot be streamed as media)."""
    return parse_post_link(value) is not None


def callback_action(call):
    """The action part of a callback payload (`action:group_id`)."""
    return (call.data or "").split(":", 1)[0]


def callback_group_id(call):
    """Group id carried by a callback payload, or the chat it was sent from."""
    parts = (call.data or "").split(":", 1)
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    message = getattr(call, "message", None)
    if message is not None and message.chat.type in ("group", "supergroup"):
        return message.chat.id
    return None


def display_user(first_name, username):
    """`Ada (@ada)` for logs, degrading gracefully when there is no username."""
    name = first_name or "Unknown"
    return f"{name} (@{username})" if username else name
