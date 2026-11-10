"""Screen senders shared by several handlers.

Keeping these here means command modules never import one another just to
re-render a menu.
"""

import logging

from database import turso as db
from utils import rich, ui
from utils.helpers import is_owner

log = logging.getLogger(__name__)


def send_main_menu(bot, chat_id, group, viewer_id=None):
    """Send the group panel with its promo slideshow."""
    show_stats = is_owner(viewer_id)
    html = ui.main_menu_rich(
        group.get("group_name"),
        db.get_stats(group["group_id"]),
        group.get("auto_approve"),
        group.get("welcome_dm"),
        show_stats=show_stats,
        suspended=group.get("suspended"),
    )
    rich.send_rich(
        bot,
        chat_id,
        html,
        ui.main_menu_keyboard(group["group_id"], show_stats=show_stats),
        media=ui.main_menu_media(),
    )


def refresh_main_menu(bot, call, group):
    """Replace the current screen with a fresh panel (media cannot be edited in)."""
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:  # noqa: BLE001 - deletion is cosmetic
        log.debug("could not delete menu message", exc_info=True)
    send_main_menu(bot, call.message.chat.id, group, viewer_id=call.from_user.id)


def send_settings(bot, chat_id, chat):
    rich.send_rich(bot, chat_id, _settings_html(chat), ui.settings_keyboard(chat, back="chats"))


def edit_settings(bot, call, chat, back=None):
    if back is None:
        back = "chats" if call.message.chat.type == "private" else "main"
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        _settings_html(chat),
        ui.settings_keyboard(chat, back=back),
    )


def edit_advanced(bot, call, chat):
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.advanced_settings_rich(chat),
        ui.advanced_settings_keyboard(chat),
    )


def send_chat_chooser(bot, chat_id, chats):
    rich.send_rich(bot, chat_id, ui.chat_chooser_rich(chats), ui.chat_chooser_keyboard(chats))


def edit_chat_chooser(bot, call, chats):
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.chat_chooser_rich(chats),
        ui.chat_chooser_keyboard(chats),
    )


def send_owner_panel(bot, chat_id):
    stats = db.get_global_stats()
    html = ui.owner_panel_rich(stats["groups"], stats["users"], stats["approved"])
    rich.send_rich(bot, chat_id, html, ui.owner_keyboard(), media=ui.owner_panel_media())


def edit_owner_panel(bot, call):
    stats = db.get_global_stats()
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.owner_panel_rich(stats["groups"], stats["users"], stats["approved"]),
        ui.owner_keyboard(),
    )


def _settings_html(chat):
    # Shared by /settings and the picker: the letterhead lists every group and
    # channel with its total, then the detail screen follows. Channel rows use
    # the channel panel so nothing is lost between the two.
    head = ui.settings_list_rich(db.get_all_chats(_PICKER_LIMIT))
    return head + (ui.channel_settings_rich(chat) if ui.is_channel(chat) else ui.settings_rich(chat))
