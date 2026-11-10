"""Force-join gate.

Every update from a non-member is answered with the join + verify screen
instead of reaching the feature handlers. Joining a group is not enough on its
own: the channels registered from /admin are checked individually, so the bot
stays silent until the user has joined all of them.

Membership lookups are the only network call in this path, so positive answers
are cached briefly. The owner is always allowed through, which keeps the admin
panel reachable when a channel is misconfigured.
"""

import logging
import time

from telebot import types
from telebot.handler_backends import BaseMiddleware, CancelUpdate

import config
from database import turso as db
from utils import helpers, pending, rich, ui

log = logging.getLogger(__name__)

_CACHE_TTL = 60
_CACHE_MAX = 5000
_cache = {}

_JOINED_STATUSES = ("creator", "administrator", "member")


class JoinGate(BaseMiddleware):
    """Middleware that stops non-members before any handler runs."""

    update_sensitive = False

    def __init__(self, bot):
        self.bot = bot
        self.update_types = [
            "message",
            "edited_message",
            "callback_query",
            "chat_join_request",
        ]

    # ── Dispatch ─────────────────────────────────────────────

    def pre_process(self, update, data):
        try:
            if isinstance(update, types.CallbackQuery):
                return self._on_callback(update)
            if isinstance(update, types.ChatJoinRequest):
                return self._on_join_request(update)
            if isinstance(update, types.Message):
                return self._on_message(update)
        except Exception:  # noqa: BLE001 - the gate must never break polling
            log.exception("join gate skipped an update")
        return None

    def post_process(self, update, data, exception):
        return None

    # ── Update types ─────────────────────────────────────────

    def _on_message(self, message):
        user = message.from_user
        if user is None or helpers.is_owner(user.id):
            return None
        if not db.get_required_channels():
            return None

        private = message.chat.type == "private"
        text = message.text or ""
        # Group chatter is left alone; only commands are gated there.
        if not private and not text.startswith("/"):
            return None

        missing = missing_channels(self.bot, user.id)
        if not missing:
            return None

        if not private and send_verify(self.bot, user.id):
            # A DM worked: keep the group clean.
            return CancelUpdate()
        send_verify(self.bot, message.chat.id)
        return CancelUpdate()

    def _on_callback(self, call):
        user = getattr(call, "from_user", None)
        if user is None or helpers.is_owner(user.id):
            return None
        if not db.get_required_channels():
            return None
        if not missing_channels(self.bot, user.id):
            return None

        message = getattr(call, "message", None)
        chat_id = message.chat.id if message is not None else user.id
        rich.answer(self.bot, call, "Join the required channels first.", alert=True)
        if not send_verify(self.bot, chat_id):
            send_verify(self.bot, user.id)
        return CancelUpdate()

    def _on_join_request(self, update):
        user = getattr(update, "from_user", None)
        chat = getattr(update, "chat", None)
        if user is None or chat is None or helpers.is_owner(user.id):
            return None
        if not db.get_required_channels():
            return None
        if not missing_channels(self.bot, user.id):
            return None

        # Left pending, never declined: the member can join the channels and
        # request again. Remembered so Verify can approve it afterwards.
        pending.remember(chat.id, user.id, user.first_name, user.username)
        log.info("join request %s in %s held: required channels missing", user.id, chat.id)
        send_verify(self.bot, user.id)
        return CancelUpdate()


# ── Membership ───────────────────────────────────────────────

def missing_channels(bot, user_id, refresh=False):
    """Channels from the force-join list the user has not joined."""
    channels = db.get_required_channels()
    if not channels:
        return []
    return [channel for channel in channels if not _is_member(bot, user_id, channel["channel_id"], refresh)]


def is_member(bot, user_id, channel_id, refresh=False):
    """Public membership check used by the verify button."""
    return _is_member(bot, user_id, channel_id, refresh)


def forget(user_id):
    """Drop cached answers for a user so the next check hits Telegram."""
    for key in [key for key in _cache if key[0] == user_id]:
        _cache.pop(key, None)


def _is_member(bot, user_id, channel_id, refresh=False):
    key = (user_id, channel_id)
    now = time.time()
    if not refresh:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    try:
        member = bot.get_chat_member(channel_id, user_id)
    except Exception:  # noqa: BLE001 - an unverifiable channel must not lock everyone out
        log.warning("could not check membership of %s in %s", user_id, channel_id, exc_info=True)
        return True

    status = getattr(member, "status", "")
    joined = status in _JOINED_STATUSES or (
        status == "restricted" and bool(getattr(member, "is_member", False))
    )
    if len(_cache) > _CACHE_MAX:
        _cache.clear()
    # Only positive answers are cached at length; a miss is re-checked on the
    # next message so joining a channel takes effect immediately.
    _cache[key] = (now + _CACHE_TTL, joined) if joined else (now + 5, joined)
    return joined


# ── Verify screen ────────────────────────────────────────────

def send_verify(bot, chat_id):
    """Send the join + verify screen. True when something was delivered."""
    channels = db.get_required_channels()
    if not channels:
        return False

    text = db.get_setting("verify_text") or config.VERIFY_TEXT
    keyboard = ui.verify_keyboard(channels)
    video = ui.verify_video_id()

    # A t.me post link is reused by copying the post, which works while the bot
    # is a member of that channel. Everything else is a file_id or https clip.
    source = helpers.parse_post_link(video)
    if source is not None:
        if _copy_post(bot, chat_id, source, keyboard):
            return True
        log.warning("verify post link %s could not be copied; using the default clip", video)
        video = config.VERIFY_VIDEO_FILE_ID

    if helpers.usable_file_id(video):
        try:
            bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=ui.verify_plain(channels, text),
                reply_markup=keyboard.to_markup(),
                supports_streaming=True,
            )
            return True
        except Exception:  # noqa: BLE001 - a bad clip falls back to the text screen
            log.warning("verify video to %s failed; sending the text screen", chat_id, exc_info=True)

    sent = rich.send_rich(bot, chat_id, ui.verify_rich(channels, text), keyboard)
    return sent is not None


def _copy_post(bot, chat_id, source, keyboard):
    """Copy the verify clip out of the channel it was posted in."""
    from_chat, message_id = source
    try:
        bot.copy_message(chat_id=chat_id, from_chat_id=from_chat, message_id=message_id)
    except Exception:  # noqa: BLE001 - not a member of that channel, deleted post, ...
        log.warning("could not copy verify post %s/%s", from_chat, message_id, exc_info=True)
        return False
    # copy_message cannot replace the caption, so the join/verify copy and its
    # buttons follow the clip as their own screen.
    return rich.send_rich(bot, chat_id, ui.verify_rich(_channels(), _text()), keyboard) is not None


def _channels():
    return db.get_required_channels()


def _text():
    return db.get_setting("verify_text") or config.VERIFY_TEXT


def approve_pending(bot, user_id):
    """Approve held join requests for a user who just passed verification."""
    approved = 0
    profile = db.get_user(user_id) or {}
    for group_id in pending.groups_for_user(user_id):
        group = db.get_group(group_id)
        if group is None:
            continue
        try:
            bot.approve_chat_join_request(group_id, user_id)
        except Exception:  # noqa: BLE001 - a vanished request is not fatal
            log.warning("could not approve held request %s in %s", user_id, group_id, exc_info=True)
            pending.forget(group_id, user_id)
            continue

        pending.forget(group_id, user_id)
        db.log_approval(user_id, group_id)
        approved += 1
        if group.get("welcome_dm"):
            rich.send_rich(
                bot,
                user_id,
                ui.welcome_dm_rich(
                    profile.get("first_name"),
                    group.get("group_name") or "the group",
                    group.get("custom_welcome"),
                ),
            )
    return approved
