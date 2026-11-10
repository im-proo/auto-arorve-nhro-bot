"""Owner panel, group/user bans and maintenance mode.

Every entry point checks is_owner() first and silently ignores anyone else.
"""

import config
from database import turso as db
from handlers import menus
from utils import helpers, rich, state, ui


def register(bot):
    @bot.message_handler(commands=["owner", "admin"])
    def handle_owner(message):
        if not helpers.is_owner(_user_id(message)):
            return
        menus.send_owner_panel(bot, message.chat.id)

    @bot.message_handler(commands=["groups"])
    def handle_groups(message):
        if not helpers.is_owner(_user_id(message)):
            return
        rich.send_rich(
            bot,
            message.chat.id,
            ui.groups_list_rich(db.get_all_groups()),
            ui.back_to_owner_keyboard(),
        )

    @bot.message_handler(commands=["globalstats"])
    def handle_global_stats(message):
        if not helpers.is_owner(_user_id(message)):
            return
        rich.send_rich(
            bot,
            message.chat.id,
            ui.global_stats_rich(db.get_global_stats()),
            ui.back_to_owner_keyboard(),
        )

    @bot.message_handler(commands=["bangroup"])
    def handle_bangroup(message):
        if not helpers.is_owner(_user_id(message)):
            return
        payload = _args(message)
        if payload:
            _ban_group(bot, message.chat.id, payload)
            return
        state.set_state(message.from_user.id, "ban_group", chat_id=message.chat.id)
        rich.send_rich(
            bot,
            message.chat.id,
            ui.prompt_rich("Ban Group", "Send the group id and an optional reason.", "Example: -1001234567890 raiding"),
            ui.back_to_owner_keyboard(),
        )

    @bot.message_handler(commands=["banuser"])
    def handle_banuser(message):
        if not helpers.is_owner(_user_id(message)):
            return
        payload = _args(message)
        if payload:
            _ban_user(bot, message.chat.id, payload)
            return
        state.set_state(message.from_user.id, "ban_user", chat_id=message.chat.id)
        rich.send_rich(
            bot,
            message.chat.id,
            ui.prompt_rich("Ban User", "Send the user id.", "Example: 123456789"),
            ui.back_to_owner_keyboard(),
        )

    @bot.message_handler(commands=["unbangroup"])
    def handle_unbangroup(message):
        if not helpers.is_owner(_user_id(message)):
            return
        group_id = _int_arg(message)
        if group_id is None:
            rich.send_rich(bot, message.chat.id, ui.error_rich("Usage: /unbangroup <group_id>"))
            return
        removed = db.unban_group(group_id)
        text = f"Group {group_id} unbanned." if removed else f"Group {group_id} was not banned."
        rich.send_rich(bot, message.chat.id, ui.success_rich(text), ui.back_to_owner_keyboard())

    @bot.message_handler(commands=["unbanuser"])
    def handle_unbanuser(message):
        if not helpers.is_owner(_user_id(message)):
            return
        user_id = _int_arg(message)
        if user_id is None:
            rich.send_rich(bot, message.chat.id, ui.error_rich("Usage: /unbanuser <user_id>"))
            return
        if not db.is_user_banned(user_id):
            rich.send_rich(bot, message.chat.id, ui.success_rich(f"User {user_id} was not banned."), ui.back_to_owner_keyboard())
            return
        db.unban_user(user_id)
        rich.send_rich(bot, message.chat.id, ui.success_rich(f"User {user_id} unbanned."), ui.back_to_owner_keyboard())

    # ── Callbacks ────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "owner_panel")
    def handle_panel(call):
        if not helpers.is_owner(call.from_user.id):
            return
        rich.answer(bot, call)
        menus.edit_owner_panel(bot, call)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "groups_list")
    def handle_groups_list(call):
        if not helpers.is_owner(call.from_user.id):
            return
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.groups_list_rich(db.get_all_groups()),
            ui.back_to_owner_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) in ("ban_group", "ban_user"))
    def handle_ban_prompt(call):
        if not helpers.is_owner(call.from_user.id):
            return
        action = helpers.callback_action(call)
        if action == "ban_group":
            state.set_state(call.from_user.id, "ban_group", chat_id=call.message.chat.id)
            html = ui.prompt_rich("Ban Group", "Send the group id and an optional reason.", "Example: -1001234567890 raiding")
        else:
            state.set_state(call.from_user.id, "ban_user", chat_id=call.message.chat.id)
            html = ui.prompt_rich("Ban User", "Send the user id.", "Example: 123456789")
        rich.answer(bot, call)
        rich.edit_rich(bot, call.message.chat.id, call.message.message_id, html, ui.back_to_owner_keyboard())

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "maintenance_toggle")
    def handle_maintenance(call):
        if not helpers.is_owner(call.from_user.id):
            return
        groups = db.get_all_groups()
        suspend = any(not group.get("suspended") for group in groups)
        db.set_all_suspended(1 if suspend else 0)
        rich.answer(bot, call, "◆ Maintenance mode ON" if suspend else "◆ Maintenance mode OFF")
        menus.edit_owner_panel(bot, call)

    # ── Prompt capture ───────────────────────────────────────

    @bot.message_handler(content_types=["text"], func=_owner_prompt)
    def capture_prompt(message):
        flow = state.current_flow(message.from_user.id)
        state.clear_state(message.from_user.id)
        if flow == "ban_group":
            _ban_group(bot, message.chat.id, (message.text or "").strip())
        else:
            _ban_user(bot, message.chat.id, (message.text or "").strip())


# ── Helpers ──────────────────────────────────────────────────

def _owner_prompt(message):
    user = message.from_user
    if user is None or not helpers.is_owner(user.id):
        return False
    if (message.text or "").startswith("/"):
        return False
    return state.current_flow(user.id) in ("ban_group", "ban_user")


def _user_id(message):
    return message.from_user.id if message.from_user else None


def _args(message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _int_arg(message):
    try:
        return int(_args(message).split()[0])
    except (ValueError, IndexError):
        return None


def _ban_group(bot, chat_id, payload):
    parts = payload.split(maxsplit=1)
    try:
        group_id = int(parts[0])
    except (ValueError, IndexError):
        rich.send_rich(bot, chat_id, ui.error_rich("That is not a group id."), ui.back_to_owner_keyboard())
        return

    reason = parts[1].strip() if len(parts) > 1 else "No reason given"
    db.ban_group(group_id, config.OWNER_ID, reason)
    # Suspend straight away so an already-running group stops approving.
    if db.get_group(group_id) is not None:
        db.update_group_setting(group_id, "suspended", 1)
    rich.send_rich(
        bot,
        chat_id,
        ui.success_rich(f"Group {group_id} banned.\nReason: {reason}"),
        ui.back_to_owner_keyboard(),
    )


def _ban_user(bot, chat_id, payload):
    try:
        user_id = int(str(payload).split()[0])
    except (ValueError, IndexError):
        rich.send_rich(bot, chat_id, ui.error_rich("That is not a user id."), ui.back_to_owner_keyboard())
        return

    db.ban_user(user_id)
    rich.send_rich(bot, chat_id, ui.success_rich(f"User {user_id} banned."), ui.back_to_owner_keyboard())
