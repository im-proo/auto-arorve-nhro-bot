"""Record groups and channels as the bot is added to them.

The Bot API cannot list the chats a bot belongs to, so membership changes are
captured from `my_chat_member` updates. That is what fills the /settings chat
picker, including channels.
"""

import logging

from database import turso as db

log = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("member", "administrator", "creator")
_SUPPORTED_TYPES = ("group", "supergroup", "channel")


def register(bot):
    @bot.my_chat_member_handler()
    def handle_membership(update):
        chat = getattr(update, "chat", None)
        member = getattr(update, "new_chat_member", None)
        if chat is None or member is None:
            return
        if getattr(chat, "type", None) not in _SUPPORTED_TYPES:
            return
        if getattr(member, "status", "") not in _ACTIVE_STATUSES:
            # Removed or kicked: keep the settings, they apply again on re-add.
            return

        actor = getattr(update, "from_user", None)
        try:
            db.add_group(
                chat.id,
                chat.title or str(chat.id),
                actor.id if actor else None,
                chat.type,
            )
        except Exception:  # noqa: BLE001 - a failed write must not crash polling
            log.warning("could not record chat %s", getattr(chat, "id", "?"), exc_info=True)
        else:
            log.info("bot is now in %s %s", chat.type, chat.id)
