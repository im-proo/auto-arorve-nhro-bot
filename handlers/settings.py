"""Chat settings: group filters, channel features and the private chat picker.

From private chat, /settings lists every group and channel the bot is in and the
caller administers, so one person can manage several chats without leaving the
bot's DM.
"""

import logging

from database import turso as db
from handlers import menus
from utils import helpers, pending, rich, state, ui

log = logging.getLogger(__name__)

_MAX_WELCOME = 3000
_PICKER_LIMIT = 20

# callback action -> (column, label)
_TOGGLES = {
    "toggle_auto_approve": ("auto_approve", "Auto Approve"),
    "toggle_auto_decline": ("auto_decline", "Auto Decline"),
    "toggle_welcome_dm": ("welcome_dm", "Welcome DM"),
    "toggle_account_age": ("account_age", "Account Age Filter"),
    "toggle_photo_req": ("photo_required", "Photo Required"),
    "toggle_username_req": ("username_req", "Username Required"),
    "toggle_schedule": ("schedule_on", "Schedule"),
    "toggle_anti_raid": ("anti_raid", "Anti Raid"),
}

# callback action -> (column, delta)
_ADJUST = {
    "age_minus": ("min_age_days", -1),
    "age_plus": ("min_age_days", 1),
    "sched_start_minus": ("schedule_start", -1),
    "sched_start_plus": ("schedule_start", 1),
    "sched_end_minus": ("schedule_end", -1),
    "sched_end_plus": ("schedule_end", 1),
}

_LABELS = {
    "min_age_days": "Min Age",
    "schedule_start": "Schedule Start",
    "schedule_end": "Schedule End",
}


def register(bot):
    # ── Commands ─────────────────────────────────────────────

    @bot.message_handler(commands=["settings"])
    def handle_settings(message):
        user = message.from_user
        if user is None:
            return

        if message.chat.type in ("group", "supergroup"):
            chat = db.get_or_add_group(
                message.chat.id,
                message.chat.title or str(message.chat.id),
                user.id,
                message.chat.type,
            )
            if not helpers.is_admin(bot, user.id, message.chat.id):
                rich.send_rich(bot, message.chat.id, ui.error_rich("Only group admins can change these settings."))
                return
            menus.send_settings(bot, message.chat.id, chat)
            return

        if message.chat.type != "private":
            return

        menus.send_chat_chooser(bot, message.chat.id, _admin_chats(bot, user.id))

    @bot.message_handler(commands=["suspend"])
    def handle_suspend(message):
        _set_suspended(bot, message, 1)

    @bot.message_handler(commands=["resume"])
    def handle_resume(message):
        _set_suspended(bot, message, 0)

    @bot.message_handler(commands=["cancel"])
    def handle_cancel(message):
        user = message.from_user
        if user is None:
            return
        had_prompt = state.pop_state(user.id) is not None
        in_group = message.chat.type in ("group", "supergroup")
        keyboard = ui.back_to_main_keyboard(message.chat.id) if in_group else ui.back_to_chats_keyboard()
        if had_prompt:
            rich.send_rich(bot, message.chat.id, ui.success_rich("Prompt cancelled."), keyboard)
        else:
            rich.send_rich(bot, message.chat.id, ui.error_rich("There is no open prompt to cancel."), keyboard)

    @bot.message_handler(commands=["setwelcome"])
    def handle_setwelcome(message):
        chat = _group_for_command(bot, message)
        if chat is None:
            return
        state.set_state(
            message.from_user.id,
            "welcome_text",
            group_id=chat["group_id"],
            chat_id=message.chat.id,
        )
        rich.send_rich(
            bot,
            message.chat.id,
            ui.prompt_rich("Welcome Message", "Send the new welcome text for this group.", _welcome_hint(chat)),
            ui.back_to_settings_keyboard(chat["group_id"]),
        )

    @bot.message_handler(commands=["resetwelcome"])
    def handle_resetwelcome(message):
        chat = _group_for_command(bot, message)
        if chat is None:
            return
        db.update_group_setting(chat["group_id"], "custom_welcome", None)
        rich.send_rich(
            bot,
            message.chat.id,
            ui.success_rich("Welcome message reset to the Nyro X default."),
            ui.back_to_settings_keyboard(chat["group_id"]),
        )

    # ── Chat picker ──────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) in ("open_chat_picker", "back_to_chats"))
    def handle_picker(call):
        chats = _admin_chats(bot, call.from_user.id)
        rich.answer(bot, call)
        menus.edit_chat_chooser(bot, call, chats)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) in ("settings_open", "pick_chat"))
    def handle_open_settings(call):
        chat = _chat_for_callback(bot, call)
        if chat is None:
            return
        rich.answer(bot, call)
        menus.edit_settings(bot, call, chat)

    # ── Toggles and thresholds ───────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) in _TOGGLES)
    def handle_toggle(call):
        column, label = _TOGGLES[helpers.callback_action(call)]
        _toggle(bot, call, column, label)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) in _ADJUST)
    def handle_adjust(call):
        _adjust(bot, call, helpers.callback_action(call))

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "accept_all")
    def handle_accept_all(call):
        chat = _chat_for_callback(bot, call)
        if chat is None:
            return
        _accept_all_toggle(bot, call, chat)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "toggle_skip_advanced")
    def handle_skip_advanced(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_group(bot, call, chat):
            return
        _skip_advanced_toggle(bot, call, chat)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "toggle_channel_pause")
    def handle_channel_pause(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_channel(bot, call, chat):
            return
        value = 0 if chat.get("suspended") else 1
        db.update_group_setting(chat["group_id"], "suspended", value)
        chat["suspended"] = value
        rich.answer(bot, call, "◆ Bot paused in this channel" if value else "◆ Bot resumed in this channel")
        menus.edit_settings(bot, call, chat)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "toggle_channel_welcome")
    def handle_channel_welcome_toggle(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_channel(bot, call, chat):
            return
        value = 0 if chat.get("welcome_dm") else 1
        db.update_group_setting(chat["group_id"], "welcome_dm", value)
        chat["welcome_dm"] = value
        rich.answer(bot, call, "◆ Channel welcome DM: ON" if value else "◆ Channel welcome DM: OFF")
        menus.edit_settings(bot, call, chat)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "channel_use_logs")
    def handle_channel_use_logs(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_channel(bot, call, chat):
            return

        applied = 0
        for group in db.get_all_chats(_PICKER_LIMIT):
            if group["group_id"] == chat["group_id"] or ui.is_channel(group):
                continue
            if helpers.is_admin(bot, call.from_user.id, group["group_id"]):
                db.update_group_setting(group["group_id"], "log_channel", str(chat["group_id"]))
                applied += 1

        if not applied:
            rich.answer(bot, call, "No groups you administer were found.", alert=True)
            return

        rich.answer(bot, call, f"◆ Approval logs now go here for {applied} group(s)")
        rich.send_rich(
            bot,
            call.message.chat.id,
            ui.success_rich(f"Approval logs for {applied} group(s) now go to {chat.get('group_name') or 'this channel'}."),
            ui.back_to_chats_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "advanced_settings")
    def handle_advanced(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_group(bot, call, chat):
            return
        rich.answer(bot, call)
        # If the group prefers to skip the details, hand them straight to the
        # pending view; otherwise show the threshold screen.
        if chat.get("skip_advanced"):
            rich.edit_rich(
                bot,
                call.message.chat.id,
                call.message.message_id,
                ui.settings_rich(chat),
                _accept_all_keyboard(chat["group_id"]),
            )
            return
        menus.edit_advanced(bot, call, chat)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "suspend_toggle")
    def handle_suspend_toggle(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_group(bot, call, chat):
            return
        value = 0 if chat.get("suspended") else 1
        db.update_group_setting(chat["group_id"], "suspended", value)
        chat["suspended"] = value
        rich.answer(bot, call, "◆ Bot suspended" if value else "◆ Bot resumed")
        menus.refresh_main_menu(bot, call, chat)

    # ── Log channel ──────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "toggle_log_channel")
    def handle_log_channel(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_group(bot, call, chat):
            return
        rich.answer(bot, call)
        state.set_state(
            call.from_user.id,
            "log_channel",
            group_id=chat["group_id"],
            chat_id=call.message.chat.id,
        )
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.log_channel_prompt_rich(chat),
            ui.log_channel_prompt_keyboard(chat["group_id"]),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "log_channel_clear")
    def handle_log_channel_clear(call):
        chat = _chat_for_callback(bot, call)
        if chat is None or not _require_group(bot, call, chat):
            return
        db.update_group_setting(chat["group_id"], "log_channel", None)
        chat["log_channel"] = None
        state.clear_state(call.from_user.id)
        rich.answer(bot, call, "◆ Log channel removed")
        menus.edit_advanced(bot, call, chat)

    @bot.message_handler(content_types=["text"], func=_capturing("log_channel"))
    def capture_log_channel(message):
        entry = state.get_state(message.from_user.id)
        chat_id = entry.get("group_id")
        if not helpers.is_admin(bot, message.from_user.id, chat_id):
            state.clear_state(message.from_user.id)
            rich.send_rich(bot, message.chat.id, ui.error_rich("Admins only."))
            return

        raw = (message.text or "").strip()
        try:
            channel = bot.get_chat(raw)
        except Exception:  # noqa: BLE001 - invalid input is expected here
            rich.send_rich(
                bot,
                message.chat.id,
                ui.error_rich("That channel could not be found. Send its @username or -100… id."),
                ui.log_channel_prompt_keyboard(chat_id),
            )
            return

        if not _can_post(bot, channel.id):
            rich.send_rich(
                bot,
                message.chat.id,
                ui.error_rich("Add me to that channel as an admin with permission to post, then send the id again."),
                ui.log_channel_prompt_keyboard(chat_id),
            )
            return

        db.update_group_setting(chat_id, "log_channel", str(channel.id))
        state.clear_state(message.from_user.id)
        title = getattr(channel, "title", None) or channel.id
        rich.send_rich(
            bot,
            message.chat.id,
            ui.success_rich(f"Log channel set to {title}."),
            ui.back_to_advanced_keyboard(chat_id),
        )

    # ── Welcome copy (groups and channels) ───────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "welcome_config")
    def handle_welcome_config(call):
        chat = _chat_for_callback(bot, call)
        if chat is None:
            return
        channel = ui.is_channel(chat)
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.welcome_config_rich(chat.get("custom_welcome"), channel=channel),
            ui.welcome_config_keyboard(chat["group_id"], back="channel" if channel else "advanced"),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "welcome_set_custom")
    def handle_welcome_set(call):
        chat = _chat_for_callback(bot, call)
        if chat is None:
            return
        channel = ui.is_channel(chat)
        state.set_state(
            call.from_user.id,
            "welcome_text",
            group_id=chat["group_id"],
            chat_id=call.message.chat.id,
        )
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.prompt_rich(
                "Welcome Message",
                f"Send the new welcome text for this {'channel' if channel else 'group'}.",
                _welcome_hint(chat),
            ),
            ui.back_to_settings_keyboard(chat["group_id"]),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "welcome_reset")
    def handle_welcome_reset(call):
        chat = _chat_for_callback(bot, call)
        if chat is None:
            return
        channel = ui.is_channel(chat)
        db.update_group_setting(chat["group_id"], "custom_welcome", None)
        chat["custom_welcome"] = None
        rich.answer(bot, call, "◆ Welcome reset")
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.welcome_config_rich(None, channel=channel),
            ui.welcome_config_keyboard(chat["group_id"], back="channel" if channel else "advanced"),
        )

    @bot.message_handler(content_types=["text"], func=_capturing("welcome_text"))
    def capture_welcome(message):
        entry = state.get_state(message.from_user.id)
        chat_id = entry.get("group_id")
        if not helpers.is_admin(bot, message.from_user.id, chat_id):
            state.clear_state(message.from_user.id)
            rich.send_rich(bot, message.chat.id, ui.error_rich("Admins only."))
            return

        text = (message.text or "").strip()
        if not text:
            rich.send_rich(bot, message.chat.id, ui.error_rich("The welcome message cannot be empty."))
            return

        db.update_group_setting(chat_id, "custom_welcome", helpers.truncate(text, _MAX_WELCOME))
        state.clear_state(message.from_user.id)
        chat = db.get_group(chat_id) or {}
        target = "Channel invitees receive it as a rich DM." if ui.is_channel(chat) else "New members receive it as a rich DM."
        rich.send_rich(
            bot,
            message.chat.id,
            ui.success_rich(f"Custom welcome saved. {target}"),
            ui.back_to_settings_keyboard(chat_id),
        )


# ── Helpers ──────────────────────────────────────────────────

def _welcome_hint(chat):
    if ui.is_channel(chat):
        return "Placeholders: {first_name} and {channel_name}\nRich HTML tags and coloured emoji are allowed."
    return "Placeholders: {first_name} and {group_name}\nRich HTML tags and coloured emoji are allowed."


def _admin_chats(bot, user_id):
    """Groups and channels the caller can manage, newest first."""
    managed = []
    for chat in db.get_all_chats(_PICKER_LIMIT):
        if helpers.is_admin(bot, user_id, chat["group_id"]):
            managed.append(chat)
    return managed


def _require_group(bot, call, chat):
    """Group-only screens must not open for a channel target."""
    if ui.is_channel(chat):
        rich.answer(bot, call, "That setting applies to groups only.", alert=True)
        return False
    return True


def _require_channel(bot, call, chat):
    """Channel-only actions must not run against a group target."""
    if not ui.is_channel(chat):
        rich.answer(bot, call, "Pick a channel first.", alert=True)
        return False
    return True


def _capturing(flow):
    """Message filter: this chat+user is mid-prompt for `flow`."""

    def predicate(message):
        user = message.from_user
        if user is None:
            return False
        if (message.text or "").startswith("/"):
            return False
        entry = state.get_state(user.id)
        return bool(entry and entry.get("flow") == flow and entry.get("chat_id") == message.chat.id)

    return predicate


def _can_post(bot, channel_id):
    """True when the bot may publish to the channel."""
    try:
        member = bot.get_chat_member(channel_id, bot.get_me().id)
    except Exception:  # noqa: BLE001 - verification is best-effort
        log.debug("could not verify channel membership", exc_info=True)
        return True
    if getattr(member, "status", "") not in ("administrator", "creator"):
        return False
    return bool(getattr(member, "can_post_messages", True))


def _group_for_command(bot, message):
    """Chat row for a command sent by an admin inside their group."""
    user = message.from_user
    if user is None:
        return None
    if message.chat.type not in ("group", "supergroup"):
        rich.send_rich(bot, message.chat.id, ui.error_rich("Run this command inside the group you want to manage."))
        return None
    chat = db.get_or_add_group(
        message.chat.id,
        message.chat.title or str(message.chat.id),
        user.id,
        message.chat.type,
    )
    if not helpers.is_admin(bot, user.id, message.chat.id):
        rich.send_rich(bot, message.chat.id, ui.error_rich("Only group admins can change these settings."))
        return None
    return chat


def _chat_for_callback(bot, call):
    """Chat row when the callback comes from one of its administrators."""
    chat_id = helpers.callback_group_id(call)
    if chat_id is None:
        rich.answer(bot, call, "Pick a chat first.", alert=True)
        return None
    chat = db.get_group(chat_id)
    if chat is None:
        rich.answer(bot, call, "That chat is not registered. Add me to it again.", alert=True)
        return None
    if not helpers.is_admin(bot, call.from_user.id, chat_id):
        rich.send_rich(bot, call.message.chat.id, ui.error_rich("Only administrators of that chat can change its settings."))
        rich.answer(bot, call, "Admins only.", alert=True)
        return None
    return chat


def _total_pending(chat):
    """Visible admission queue: approved-by-this-bot plus requests not yet seen."""
    approved = db.get_stats(chat["group_id"])
    queue = pending.get(chat["group_id"])
    return approved, (len(queue) if queue else 0)


def _accept_all_toggle(bot, call, chat):
    """Admit every cached pending request for this chat."""
    if not helpers.is_admin(bot, call.from_user.id, chat["group_id"]):
        return
    group = db.get_group(chat["group_id"])
    if group is None or group.get("suspended"):
        return

    queue = pending.get(chat["group_id"]) or {}
    accepted = 0
    for user_id, info in queue.items():
        try:
            bot.approve_chat_join_request(chat["group_id"], user_id)
        except Exception:  # noqa: BLE001 - a vanished request is not fatal
            log.warning("accept-all failed for %s", user_id, exc_info=True)
            pending.forget(chat["group_id"], user_id)
            continue

        pending.forget(chat["group_id"], user_id)
        db.log_approval(user_id, chat["group_id"])
        db.add_user(user_id, info.get("first_name"), info.get("username"))
        db.mark_user_started(user_id)
        accepted += 1
        if group.get("welcome_dm"):
            rich.send_rich(
                bot,
                user_id,
                ui.welcome_dm_rich(
                    info.get("first_name"),
                    group.get("group_name") or "the group",
                    group.get("custom_welcome"),
                ),
            )

    rich.answer(bot, call, f"◆ Accepted {accepted} request(s).")


def _skip_advanced_toggle(bot, call, chat):
    """Skip the advanced screen and go straight to accepting requests."""
    chat = _chat_for_callback(bot, call)
    if chat is None or not _require_group(bot, call, chat):
        return
    value = 0 if chat.get("skip_advanced") else 1
    db.update_group_setting(chat["group_id"], "skip_advanced", value)
    chat["skip_advanced"] = value
    rich.answer(bot, call, f"◆ Skip advanced: {ui._onoff(value)}")
    menus.edit_settings(bot, call, chat)


def _toggle(bot, call, column, label):
    chat = _chat_for_callback(bot, call)
    if chat is None or not _require_group(bot, call, chat):
        return
    value = 0 if chat.get(column) else 1
    db.update_group_setting(chat["group_id"], column, value)
    chat[column] = value
    rich.answer(bot, call, f"◆ Setting updated — {label}: {'ON' if value else 'OFF'}")

    # Turning on a filter opens the screen where its threshold is set.
    if value and column in ("account_age", "schedule_on"):
        menus.edit_advanced(bot, call, chat)
    else:
        menus.edit_settings(bot, call, chat)


def _adjust(bot, call, action):
    """Step a numeric threshold up or down and redraw the advanced screen."""
    chat = _chat_for_callback(bot, call)
    if chat is None or not _require_group(bot, call, chat):
        return

    column, delta = _ADJUST[action]
    current = int(chat.get(column) or 0)
    if column == "min_age_days":
        value = max(0, min(365, current + delta))
        suffix = "d"
    else:
        value = (current + delta) % 24
        suffix = "h"
    db.update_group_setting(chat["group_id"], column, value)
    chat[column] = value
    rich.answer(bot, call, f"◆ {_LABELS[column]}: {value}{suffix}")
    menus.edit_advanced(bot, call, chat)


def _accept_all_keyboard(chat_id):
    """Starter row for the pending view: accept everything waiting now."""
    return ui.Keyboard(
        [ui.Button(f"◈ {ui._l('Accept All')}  ▸", ui._cb("accept_all", chat_id), style=ui.SUCCESS)],
        [ui.Button(f"◇ {ui._l('Settings')}", ui._cb("settings_open", chat_id), style=ui.DANGER)],
    )


def _set_suspended(bot, message, value):
    chat = _group_for_command(bot, message)
    if chat is None:
        return
    db.update_group_setting(chat["group_id"], "suspended", value)
    text = "Bot suspended in this group." if value else "Bot resumed in this group."
    rich.send_rich(bot, message.chat.id, ui.success_rich(text), ui.back_to_main_keyboard(chat["group_id"]))
