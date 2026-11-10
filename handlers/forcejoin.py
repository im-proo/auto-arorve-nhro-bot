"""Force-join channels, the verify screen and the media links behind them.

Everything here is owner-only: the panel is reached from /admin (the same panel
/owner opens). Required channels are stored in Turso, so a redeploy keeps them.
"""

import logging

from database import turso as db
from utils import gate, helpers, rich, state, ui

log = logging.getLogger(__name__)

MAX_CHANNELS = 5


def register(bot):
    # ── Panels ───────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "force_join")
    def handle_force_join(call):
        if not helpers.is_owner(call.from_user.id):
            return
        rich.answer(bot, call)
        _edit_panel(bot, call)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "media_config")
    def handle_media(call):
        if not helpers.is_owner(call.from_user.id):
            return
        rich.answer(bot, call)
        _edit_media(bot, call)

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "media_reset")
    def handle_media_reset(call):
        if not helpers.is_owner(call.from_user.id):
            return
        for key in ("menu_video", "verify_video", "verify_text"):
            db.delete_setting(key)
        ui.clear_media_cache()
        rich.answer(bot, call, "◆ Defaults restored")
        _edit_media(bot, call)

    # ── Channels ─────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "channel_add")
    def handle_channel_add(call):
        if not helpers.is_owner(call.from_user.id):
            return
        if len(db.get_required_channels()) >= MAX_CHANNELS:
            rich.answer(bot, call, f"Only {MAX_CHANNELS} channels can be required.", alert=True)
            return
        state.set_state(call.from_user.id, "add_channel", chat_id=call.message.chat.id)
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.prompt_rich(
                "Add Channel",
                "Send the channel @username or -100… id.",
                "Add me to that channel as an administrator first\n"
                "(Invite Users is enough). The invite link is\n"
                "created automatically.",
            ),
            ui.back_to_owner_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "channel_del")
    def handle_channel_del(call):
        if not helpers.is_owner(call.from_user.id):
            return
        channel_id = helpers.callback_group_id(call)
        if channel_id is None:
            return
        removed = db.remove_required_channel(channel_id)
        rich.answer(bot, call, "◆ Channel removed" if removed else "Channel was not required")
        _edit_panel(bot, call)

    # ── Verify copy and media ────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "verify_video_set")
    def handle_verify_video(call):
        if not helpers.is_owner(call.from_user.id):
            return
        _prompt_video(bot, call, "verify_video", "Verify Video")

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "menu_video_set")
    def handle_menu_video(call):
        if not helpers.is_owner(call.from_user.id):
            return
        _prompt_video(bot, call, "menu_video", "Main Menu Video")

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "verify_text_set")
    def handle_verify_text(call):
        if not helpers.is_owner(call.from_user.id):
            return
        state.set_state(call.from_user.id, "verify_text", chat_id=call.message.chat.id)
        rich.answer(bot, call)
        rich.edit_rich(
            bot,
            call.message.chat.id,
            call.message.message_id,
            ui.prompt_rich(
                "Verify Text",
                "Send the text shown on the join and verify screen.",
                "Plain text is fine. It appears above the buttons.",
            ),
            ui.back_to_owner_keyboard(),
        )

    # ── Verify button ────────────────────────────────────────

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call) == "verify_check")
    def handle_verify_check(call):
        user = call.from_user
        missing = gate.missing_channels(bot, user.id, refresh=True)
        if missing:
            names = ", ".join(channel.get("title") or str(channel.get("channel_id")) for channel in missing)
            rich.answer(bot, call, f"Join first: {names}", alert=True)
            chat_id = call.message.chat.id if call.message is not None else user.id
            gate.send_verify(bot, chat_id)
            return

        gate.forget(user.id)
        db.add_user(user.id, user.first_name, user.username)
        db.mark_user_started(user.id)
        approved = gate.approve_pending(bot, user.id)
        rich.answer(bot, call, "◆ Verified")

        chat = call.message.chat
        try:
            bot.delete_message(chat.id, call.message.message_id)
        except Exception:  # noqa: BLE001 - cosmetic
            log.debug("could not delete the verify screen", exc_info=True)

        if chat.type == "private":
            rich.send_rich(
                bot,
                chat.id,
                ui.private_welcome_rich(user.first_name),
                ui.private_welcome_keyboard(),
            )
            return

        text = "Verified — you can use the bot now."
        if approved:
            text += f"\n{approved} join request(s) approved."
        rich.send_rich(bot, chat.id, ui.success_rich(text))

    # ── Prompt capture ───────────────────────────────────────

    @bot.message_handler(
        content_types=["text", "video", "animation", "document"],
        func=_capturing("add_channel", "verify_text", "verify_video", "menu_video"),
    )
    def capture_prompt(message):
        user = message.from_user
        flow = state.current_flow(user.id)
        state.pop_state(user.id)

        if flow == "add_channel":
            _add_channel(bot, message.chat.id, (message.text or "").strip())
            return

        if flow == "verify_text":
            text = (message.text or "").strip()
            if not text:
                rich.send_rich(bot, message.chat.id, ui.error_rich("The verify text cannot be empty."))
                return
            db.set_setting("verify_text", helpers.truncate(text, 1500))
            ui.clear_media_cache()
            rich.send_rich(
                bot,
                message.chat.id,
                ui.success_rich("Verify text saved."),
                ui.back_to_owner_keyboard(),
            )
            return

        value = _video_value(message)
        if not value:
            rich.send_rich(
                bot,
                message.chat.id,
                ui.error_rich("Send a video, a file id, or a direct https video link."),
                ui.back_to_owner_keyboard(),
            )
            return

        db.set_setting(flow, value)
        ui.clear_media_cache()
        label = "Verify video" if flow == "verify_video" else "Main menu video"
        rich.send_rich(
            bot,
            message.chat.id,
            ui.success_rich(f"{label} updated."),
            ui.back_to_owner_keyboard(),
        )


# ── Helpers ──────────────────────────────────────────────────

def _edit_panel(bot, call):
    channels = db.get_required_channels()
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.force_join_rich(channels, ui.verify_video_id()),
        ui.force_join_keyboard(channels),
    )


def _edit_media(bot, call):
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.media_rich(ui.menu_video_id(), ui.verify_video_id()),
        ui.media_keyboard(),
    )


def _prompt_video(bot, call, flow, title):
    state.set_state(call.from_user.id, flow, chat_id=call.message.chat.id)
    rich.answer(bot, call)
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.prompt_rich(
            title,
            "Send the clip, a file id, a direct https video link, or a t.me post link.",
            "Forward the video to me and the file id is captured.\n"
            "A t.me/c/… post link is reused by copying the post,\n"
            "so I must be a member of that channel.",
        ),
        ui.back_to_owner_keyboard(),
    )


def _capturing(*flows):
    allowed = frozenset(flows)

    def predicate(message):
        user = message.from_user
        if user is None or not helpers.is_owner(user.id):
            return False
        if (message.text or "").startswith("/"):
            return False
        entry = state.get_state(user.id)
        return bool(entry and entry.get("flow") in allowed and entry.get("chat_id") == message.chat.id)

    return predicate


def _video_value(message):
    """file_id from a sent clip, or a typed file_id / https link."""
    for attribute in ("video", "animation"):
        media = getattr(message, attribute, None)
        file_id = getattr(media, "file_id", None)
        if file_id:
            return file_id
    document = getattr(message, "document", None)
    if document is not None and str(getattr(document, "mime_type", "") or "").startswith("video"):
        return document.file_id
    text = (message.text or "").strip()
    return text or None


def _add_channel(bot, chat_id, raw):
    if not raw:
        rich.send_rich(bot, chat_id, ui.error_rich("Send the channel @username or -100… id."))
        return

    try:
        chat = bot.get_chat(raw)
    except Exception:  # noqa: BLE001 - invalid input is expected here
        rich.send_rich(
            bot,
            chat_id,
            ui.error_rich("That channel could not be found. Add me to it first, then send its id."),
            ui.back_to_owner_keyboard(),
        )
        return

    if getattr(chat, "type", "") != "channel":
        rich.send_rich(
            bot,
            chat_id,
            ui.error_rich("That is not a channel."),
            ui.back_to_owner_keyboard(),
        )
        return

    if not _is_channel_admin(bot, chat.id):
        rich.send_rich(
            bot,
            chat_id,
            ui.error_rich(
                "I am not an administrator in that channel.\n"
                "Promote me with the Invite Users right, then send the id again."
            ),
            ui.back_to_owner_keyboard(),
        )
        return

    link = _invite_link(bot, chat)
    if not link:
        rich.send_rich(
            bot,
            chat_id,
            ui.error_rich(
                "I could not create an invite link.\n"
                "Check that I can invite users in that channel and try again."
            ),
            ui.back_to_owner_keyboard(),
        )
        return

    db.add_required_channel(chat.id, getattr(chat, "title", None) or str(chat.id), link)
    channels = db.get_required_channels()
    rich.send_rich(
        bot,
        chat_id,
        ui.success_rich(f"{getattr(chat, 'title', None) or chat.id} is now required."),
        ui.force_join_keyboard(channels),
    )
    rich.send_rich(bot, chat_id, ui.force_join_rich(channels, ui.verify_video_id()))


def _is_channel_admin(bot, channel_id):
    try:
        member = bot.get_chat_member(channel_id, bot.get_me().id)
    except Exception:  # noqa: BLE001 - verification is best-effort but reported
        log.warning("could not verify admin rights in %s", channel_id, exc_info=True)
        return False
    return getattr(member, "status", "") in ("administrator", "creator")


def _invite_link(bot, chat):
    """Create a fresh invite link, falling back to the public username."""
    try:
        created = bot.create_chat_invite_link(chat.id, name="Required join")
        link = getattr(created, "invite_link", None)
        if link:
            return link
    except Exception:  # noqa: BLE001 - public channels fall back to t.me/username
        log.warning("could not create an invite link for %s", chat.id, exc_info=True)

    username = getattr(chat, "username", None)
    return f"https://t.me/{username}" if username else None
