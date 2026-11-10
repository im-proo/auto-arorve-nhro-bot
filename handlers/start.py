"""Start, help and the private greeting.

A channel invite link points at the bot (`t.me/<bot>?start=ch_<channel_id>`), so
opening it delivers that channel's welcome DM. Channels expose no join events,
so this is the honest way to greet their audience.
"""

from database import turso as db
from handlers import menus
from utils import rich, ui
from utils.helpers import callback_action, callback_group_id, is_admin, is_owner

_CHANNEL_PREFIX = "ch_"


def register(bot):
    @bot.message_handler(commands=["start"])
    def handle_start(message):
        user = message.from_user
        if user is None:
            return

        db.add_user(user.id, user.first_name, user.username)
        db.mark_user_started(user.id)

        channel_id = _channel_payload(message)
        if channel_id is not None:
            send_channel_welcome(bot, user, message.chat.id, channel_id)
            return

        if is_owner(user.id) and message.chat.type == "private":
            menus.send_owner_panel(bot, message.chat.id)
            return

        group_id = _group_context(message)
        if group_id is not None:
            group = db.get_or_add_group(
                group_id,
                message.chat.title or str(group_id),
                user.id,
                message.chat.type if message.chat.type in ("group", "supergroup") else "group",
            )
            if not is_admin(bot, user.id, group_id):
                rich.send_rich(bot, message.chat.id, ui.error_rich("Only group admins can open this panel."))
                return
            menus.send_main_menu(bot, message.chat.id, group, viewer_id=user.id)
            return

        rich.send_rich(
            bot,
            message.chat.id,
            ui.private_welcome_rich(user.first_name),
            ui.private_welcome_keyboard(),
        )

    @bot.message_handler(commands=["help"])
    def handle_help(message):
        if message.from_user is None:
            return
        rich.send_rich(bot, message.chat.id, ui.help_rich(is_owner(message.from_user.id)))

    @bot.callback_query_handler(func=lambda call: callback_action(call) in ("back_to_main", "help", "home"))
    def handle_menu_callbacks(call):
        action = callback_action(call)

        if action == "help":
            rich.answer(bot, call)
            rich.send_rich(bot, call.message.chat.id, ui.help_rich(is_owner(call.from_user.id)))
            return

        if action == "home":
            rich.answer(bot, call)
            rich.edit_rich(
                bot,
                call.message.chat.id,
                call.message.message_id,
                ui.private_welcome_rich(call.from_user.first_name),
                ui.private_welcome_keyboard(),
            )
            return

        group_id = callback_group_id(call)
        if group_id is None:
            rich.answer(bot, call, "Open this from your group.", alert=True)
            return

        group = db.get_group(group_id)
        if group is None:
            rich.answer(bot, call, "This group has no settings yet. Send /start in the group.", alert=True)
            return
        if not is_admin(bot, call.from_user.id, group_id):
            rich.answer(bot, call, "Admins only.", alert=True)
            return

        rich.answer(bot, call)
        menus.refresh_main_menu(bot, call, group)


def send_channel_welcome(bot, user, chat_id, channel_id):
    """Greet a user who opened the bot from a channel invite link."""
    chat = None
    try:
        chat = bot.get_chat(channel_id)
    except Exception:  # noqa: BLE001 - a stale link must not break /start
        pass

    if chat is not None and getattr(chat, "type", "") != "channel":
        chat = None

    group = db.get_group(channel_id)
    if group is None and chat is not None:
        db.add_group(channel_id, chat.title or str(channel_id), user.id, "channel")
        group = db.get_group(channel_id)

    if group is not None and not group.get("welcome_dm"):
        # Welcome DMs are switched off for this channel: show the normal greeting.
        rich.send_rich(bot, chat_id, ui.private_welcome_rich(user.first_name), ui.private_welcome_keyboard())
        return

    title = (getattr(chat, "title", None) or (group or {}).get("group_name") or "the channel")
    html = ui.channel_welcome_dm_rich(
        user.first_name,
        title,
        (group or {}).get("custom_welcome"),
    )
    keyboard = ui.channel_welcome_keyboard(getattr(chat, "username", None))
    rich.send_rich(bot, chat_id, html, keyboard)


def _channel_payload(message):
    """Channel id from a `?start=ch_<id>` deep link, or None."""
    payload = _payload(message)
    if payload is None or not payload.startswith(_CHANNEL_PREFIX):
        return None
    try:
        return int(payload[len(_CHANNEL_PREFIX):])
    except ValueError:
        return None


def _group_context(message):
    """Group id from the chat itself, or from a /start deep-link payload."""
    if message.chat.type in ("group", "supergroup"):
        return message.chat.id

    payload = _payload(message)
    if payload is None or payload.startswith(_CHANNEL_PREFIX):
        return None

    for prefix in ("group_", "g_"):
        if payload.startswith(prefix):
            payload = payload[len(prefix):]
            break
    else:
        if payload.startswith("g"):
            payload = payload[1:]

    try:
        return int(payload)
    except ValueError:
        return None


def _payload(message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None
