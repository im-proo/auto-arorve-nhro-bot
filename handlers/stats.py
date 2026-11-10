"""Approval statistics for a group, plus global totals.

Statistics are owner-only: group admins never see counts or the stats button.
"""

from database import turso as db
from utils import helpers, rich, ui
from utils.helpers import is_owner


def register(bot):
    @bot.message_handler(commands=["stats"])
    def handle_stats(message):
        user = message.from_user
        if user is None:
            return
        if not is_owner(user.id):
            rich.send_rich(bot, message.chat.id, ui.error_rich("Statistics are owner-only."))
            return

        group_id = None
        if message.chat.type in ("group", "supergroup"):
            group_id = message.chat.id
        else:
            group_id = _group_id_from_text(message.text)

        if group_id is None:
            rich.send_rich(bot, message.chat.id, ui.global_stats_rich(db.get_global_stats()))
            return

        group = db.get_group(group_id)
        if group is None:
            rich.send_rich(bot, message.chat.id, ui.error_rich("No statistics yet for that group."))
            return
        rich.send_rich(bot, message.chat.id, _stats_html(group), ui.stats_keyboard(group["group_id"]))

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "stats")
    def handle_stats_callback(call):
        if not is_owner(call.from_user.id):
            rich.answer(bot, call)
            return
        group_id = helpers.callback_group_id(call)
        group = db.get_group(group_id) if group_id is not None else None
        if group is None:
            rich.answer(bot, call, "No statistics yet for this group.", alert=True)
            return
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            _stats_html(group),
            ui.stats_keyboard(group["group_id"]),
        )


def _stats_html(group):
    totals = db.get_global_stats()
    return ui.stats_rich(
        group.get("group_name"),
        db.get_stats(group["group_id"]),
        totals["groups"],
        totals["users"],
        global_approved=totals["approved"],
        last_approved=db.get_last_approval(group["group_id"]),
    )


def _group_id_from_text(text):
    """/stats <group_id> so the owner can inspect any chat from private chat."""
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None
