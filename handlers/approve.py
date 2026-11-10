"""Join-request auto approval plus bulk approve/decline.

The decision path is deliberately ordered: hard blocks first (banned group,
banned user, suspended), then the group's filters, then the approval itself.
Anything that fails a check is left pending rather than declined, so a member
can still be admitted manually.
"""

import logging
import threading
import time
from collections import deque

import config
from database import turso as db
from utils import helpers, pending, rich, ui

log = logging.getLogger(__name__)

# Filters that may decline the request instead of leaving it pending. Hard
# blocks (bans, suspension, auto-approve off) are never declined automatically.
_DECLINABLE = frozenset({
    "outside schedule",
    "account too new",
    "no profile photo",
    "no username",
    "anti raid",
})

_recent = {}
_recent_lock = threading.Lock()


class _RequestUser:
    """Minimal stand-in for a Telegram User during bulk approvals."""

    __slots__ = ("id", "first_name", "username")

    def __init__(self, user_id, first_name=None, username=None):
        self.id = user_id
        self.first_name = first_name
        self.username = username


def register(bot):
    @bot.chat_join_request_handler()
    def handle_join_request(update):
        _process(bot, update)

    @bot.message_handler(commands=["approveall"])
    def handle_approveall(message):
        _bulk(bot, message, approve=True)

    @bot.message_handler(commands=["declineall"])
    def handle_declineall(message):
        _bulk(bot, message, approve=False)


# ── Single request ───────────────────────────────────────────

def _process(bot, update):
    chat = getattr(update, "chat", None)
    user = getattr(update, "from_user", None)
    if chat is None or user is None:
        return

    group = db.get_group(chat.id)
    if group is None:
        db.add_group(chat.id, chat.title or str(chat.id), None, getattr(chat, "type", "group"))
        group = db.get_group(chat.id)

    # Remembered so /approveall can act on requests the bot saw but skipped.
    pending.remember(chat.id, user.id, user.first_name, user.username)

    reason = _rejection_reason(bot, group, user)
    if reason is not None:
        log.info("request %s in %s rejected: %s", user.id, chat.id, reason)
        if _should_decline(group, reason):
            _decline(bot, group, user, reason)
        return

    try:
        bot.approve_chat_join_request(chat.id, user.id)
    except Exception:  # noqa: BLE001 - a vanished request is not fatal
        log.warning("could not approve %s in %s", user.id, chat.id, exc_info=True)
        return

    pending.forget(chat.id, user.id)
    _record_approval(chat.id)
    db.log_approval(user.id, chat.id)
    db.add_user(user.id, user.first_name, user.username)
    db.mark_user_started(user.id)

    if group.get("welcome_dm"):
        _send_welcome(bot, user, group)
    if group.get("log_channel"):
        _log_approval(bot, group, user)


def _should_decline(group, reason):
    """True when this group declines failed filters instead of leaving them pending."""
    return bool(group.get("auto_decline")) and reason in _DECLINABLE


def _decline(bot, group, user, reason):
    """Decline a failed request and report it to the group's log channel."""
    try:
        bot.decline_chat_join_request(group["group_id"], user.id)
    except Exception:  # noqa: BLE001 - an already-gone request is not fatal
        log.warning("could not decline %s in %s", user.id, group["group_id"], exc_info=True)
        return

    pending.forget(group["group_id"], user.id)
    if group.get("log_channel"):
        rich.send_plain(bot, group["log_channel"], "\n".join([
            "◆ Declined",
            f"‣ User: {helpers.display_user(user.first_name, user.username)}",
            f"‣ ID: {user.id}",
            f"‣ Group: {group.get('group_name') or group['group_id']}",
            f"‣ Reason: {reason}",
            f"‣ Time: {helpers.format_timestamp()}",
        ]))


def _rejection_reason(bot, group, user):
    """None when the request may be approved, else a short log reason."""
    if db.is_group_banned(group["group_id"]):
        return "group banned"
    if db.is_user_banned(user.id):
        return "user banned"
    if group.get("suspended"):
        return "bot suspended"
    if not group.get("auto_approve"):
        return "auto approve off"

    if group.get("schedule_on") and not helpers.is_within_schedule(
        group.get("schedule_start") or 0, group.get("schedule_end") or 0
    ):
        return "outside schedule"

    if group.get("account_age"):
        minimum = int(group.get("min_age_days") or 0)
        if helpers.get_account_age_days(user.id) < minimum:
            return "account too new"

    if group.get("photo_required") and not _has_profile_photo(bot, user.id):
        return "no profile photo"

    if group.get("username_req") and not user.username:
        return "no username"

    if group.get("anti_raid") and _raid_limited(group["group_id"]):
        return "anti raid"
    return None


def _has_profile_photo(bot, user_id):
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
    except Exception:  # noqa: BLE001 - treat an API hiccup as "no photo"
        log.warning("profile photo lookup failed for %s", user_id, exc_info=True)
        return False
    return bool(getattr(photos, "total_count", 0))


def _send_welcome(bot, user, group):
    html = ui.welcome_dm_rich(
        user.first_name,
        group.get("group_name") or "the group",
        group.get("custom_welcome"),
    )
    rich.send_rich(bot, user.id, html)


def _log_approval(bot, group, user):
    lines = [
        "◆ Approved",
        f"‣ User: {helpers.display_user(user.first_name, user.username)}",
        f"‣ ID: {user.id}",
        f"‣ Group: {group.get('group_name') or group['group_id']}",
        f"‣ Time: {helpers.format_timestamp()}",
    ]
    rich.send_plain(bot, group["log_channel"], "\n".join(lines))


# ── Anti-raid window ─────────────────────────────────────────

def _raid_limited(group_id):
    """True when the group is approving faster than the anti-raid ceiling."""
    now = time.time()
    with _recent_lock:
        if len(_recent) > 2000:
            for key in [k for k, window in _recent.items() if not window or now - window[-1] > config.ANTI_RAID_WINDOW]:
                _recent.pop(key, None)
        window = _recent.setdefault(group_id, deque(maxlen=config.ANTI_RAID_MAX_APPROVALS * 2))
        while window and now - window[0] > config.ANTI_RAID_WINDOW:
            window.popleft()
        return len(window) >= config.ANTI_RAID_MAX_APPROVALS


def _record_approval(group_id):
    with _recent_lock:
        _recent.setdefault(group_id, deque(maxlen=config.ANTI_RAID_MAX_APPROVALS * 2)).append(time.time())


# ── Bulk ─────────────────────────────────────────────────────

def _bulk(bot, message, approve):
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        rich.send_rich(bot, message.chat.id, ui.error_rich("Run this command inside the group."))
        return
    if not helpers.is_admin(bot, user.id, message.chat.id):
        rich.send_rich(bot, message.chat.id, ui.error_rich("Only group admins can do that."))
        return

    group = db.get_or_add_group(
        message.chat.id,
        message.chat.title or str(message.chat.id),
        user.id,
        message.chat.type,
    )
    if db.is_group_banned(message.chat.id) or group.get("suspended"):
        rich.send_rich(bot, message.chat.id, ui.error_rich("This group is suspended or banned."))
        return

    targets = pending.get(message.chat.id)
    if not targets:
        rich.send_rich(
            bot,
            message.chat.id,
            ui.error_rich(
                "No pending requests are cached.\n"
                "The Bot API only delivers requests as they arrive, so this list starts empty after a restart."
            ),
        )
        return

    done = failed = 0
    for user_id, info in targets.items():
        try:
            # A manual bulk run overrides the member-quality filters on purpose:
            # the admin is asking for exactly these cached requests.
            if approve:
                bot.approve_chat_join_request(message.chat.id, user_id)
                db.log_approval(user_id, message.chat.id)
                db.add_user(user_id, info.get("first_name"), info.get("username"))
                db.mark_user_started(user_id)
                if group.get("welcome_dm"):
                    _send_welcome(bot, _RequestUser(user_id, info.get("first_name"), info.get("username")), group)
            else:
                bot.decline_chat_join_request(message.chat.id, user_id)
            done += 1
            pending.forget(message.chat.id, user_id)
        except Exception:  # noqa: BLE001 - one dead request must not stop the run
            failed += 1
            log.warning("bulk %s failed for %s", "approve" if approve else "decline", user_id, exc_info=True)
        time.sleep(0.05)

    verb = "Approved" if approve else "Declined"
    rich.send_rich(
        bot,
        message.chat.id,
        ui.success_rich(f"{verb} {done} request(s).\nFailed: {failed}."),
        ui.back_to_main_keyboard(message.chat.id),
    )
