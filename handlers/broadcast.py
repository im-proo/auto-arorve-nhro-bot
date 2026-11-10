"""Owner broadcast flow: type, content, preview, confirm, deliver, report."""

import logging
import time

import config
from database import turso as db
from utils import helpers, rich, state, ui

log = logging.getLogger(__name__)

_KINDS = {
    "broadcast_type_text": "text",
    "broadcast_type_photo": "photo",
    "broadcast_type_video": "video",
    "broadcast_type_rich": "rich",
}

_KIND_LABELS = {"text": "TEXT", "photo": "PHOTO", "video": "VIDEO", "rich": "RICH CARD"}


def register(bot):
    @bot.message_handler(commands=["broadcast"])
    def handle_broadcast(message):
        if not helpers.is_owner(_user_id(message)):
            return
        rich.send_rich(bot, message.chat.id, ui.broadcast_menu_rich(), ui.broadcast_keyboard())

    @bot.callback_query_handler(func=lambda call: helpers.callback_action(call).startswith("broadcast_"))
    def handle_callback(call):
        if not helpers.is_owner(call.from_user.id):
            return

        action = helpers.callback_action(call)
        if action in _KINDS:
            _choose_kind(bot, call, _KINDS[action])
        elif action == "broadcast_start":
            _open_menu(bot, call)
        elif action == "broadcast_target_users":
            rich.answer(bot, call, "Only DM delivery is available.", alert=True)
        elif action == "broadcast_preview":
            _preview_staged(bot, call)
        elif action == "broadcast_cancel":
            _cancel(bot, call)
        elif action == "broadcast_confirm":
            _run(bot, call)

    @bot.message_handler(content_types=["text", "photo", "video"], func=_capturing)
    def capture_content(message):
        entry = state.get_state(message.from_user.id) or {}
        kind = entry.get("kind")
        draft = _draft_from_message(message, kind)
        if draft is None:
            rich.send_rich(
                bot,
                message.chat.id,
                ui.error_rich(f"Send a {kind} message for this broadcast, or tap Cancel."),
                ui.broadcast_cancel_keyboard(),
            )
            return

        state.set_state(
            message.from_user.id,
            "broadcast_confirm",
            draft=draft,
            chat_id=entry.get("chat_id"),
            message_id=entry.get("message_id"),
        )
        _show_preview(bot, message.chat.id, draft)


# ── Flow steps ───────────────────────────────────────────────

def _open_menu(bot, call):
    if call.message.chat.type != "private":
        rich.answer(bot, call, "Open the owner panel in a private chat with me.", alert=True)
        return
    rich.answer(bot, call)
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.broadcast_menu_rich(),
        ui.broadcast_keyboard(),
    )


def _choose_kind(bot, call, kind):
    state.set_state(
        call.from_user.id,
        "broadcast_content",
        kind=kind,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )
    rich.answer(bot, call, f"◆ {_KIND_LABELS[kind]} selected")
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.broadcast_prompt_rich(kind),
        ui.broadcast_cancel_keyboard(),
    )


def _draft_from_message(message, kind):
    """Validate the owner's message against the chosen type."""
    content_type = message.content_type

    if kind in ("text", "rich"):
        if content_type != "text" or not (message.text or "").strip():
            return None
        return {"kind": kind, "text": message.text}

    if kind == "photo":
        if content_type != "photo":
            return None
        return {"kind": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption}

    if kind == "video":
        if content_type != "video":
            return None
        return {"kind": "video", "file_id": message.video.file_id, "caption": message.caption}

    return None


def _preview_staged(bot, call):
    entry = state.get_state(call.from_user.id) or {}
    draft = entry.get("draft")
    if not draft:
        rich.answer(bot, call, "Pick a type and send the content first.", alert=True)
        return
    rich.answer(bot, call)
    _show_preview(bot, call.message.chat.id, draft)


def _show_preview(bot, chat_id, draft):
    count = len(db.get_all_started_users())
    # Echo the payload first so the owner sees exactly what recipients get.
    try:
        _deliver(bot, chat_id, draft, silent=True)
    except Exception:  # noqa: BLE001 - preview failure must not block confirmation
        log.info("broadcast preview echo failed", exc_info=True)

    rich.send_rich(
        bot,
        chat_id,
        ui.confirmation_rich(_summary(draft), count),
        ui.confirmation_keyboard(),
    )


def _cancel(bot, call):
    state.clear_state(call.from_user.id)
    rich.answer(bot, call, "Broadcast cancelled.")
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.success_rich("Broadcast cancelled. Nothing was sent."),
        ui.back_to_owner_keyboard(),
    )


def _run(bot, call):
    entry = state.get_state(call.from_user.id) or {}
    draft = entry.get("draft")
    if not draft:
        rich.answer(bot, call, "Nothing staged. Start a broadcast first.", alert=True)
        return

    state.clear_state(call.from_user.id)
    users = db.get_all_started_users()
    rich.answer(bot, call, "◆ Sending…")
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.prompt_rich("Broadcast", f"Sending to {helpers.format_number(len(users))} users…"),
        None,
    )

    started = time.time()
    sent = failed = 0
    for user_id in users:
        try:
            if _deliver(bot, user_id, draft) is None:
                failed += 1
            else:
                sent += 1
        except Exception as exc:  # noqa: BLE001 - one blocked user must not stop the run
            failed += 1
            pause = _retry_after(exc)
            if pause:
                time.sleep(pause)
                try:
                    _deliver(bot, user_id, draft)
                except Exception:  # noqa: BLE001
                    log.debug("retry after flood wait failed for %s", user_id)
        time.sleep(config.BROADCAST_DELAY)

    elapsed = int(time.time() - started)
    db.log_broadcast(call.from_user.id, _summary(draft), sent, failed)
    log.info("broadcast by %s: sent=%s failed=%s in %ss", call.from_user.id, sent, failed, elapsed)
    rich.edit_rich(
        bot,
        call.message.chat.id,
        call.message.message_id,
        ui.broadcast_report_rich(sent, failed, elapsed),
        ui.back_to_owner_keyboard(),
    )


# ── Delivery ─────────────────────────────────────────────────

def _deliver(bot, user_id, draft, silent=False):
    kind = draft["kind"]
    if kind == "text":
        return bot.send_message(user_id, draft["text"], disable_web_page_preview=True)
    if kind == "photo":
        return bot.send_photo(user_id, draft["file_id"], caption=draft.get("caption") or None)
    if kind == "video":
        return bot.send_video(user_id, draft["file_id"], caption=draft.get("caption") or None)
    return rich.send_rich(bot, user_id, draft["text"], silent=silent)


def _summary(draft):
    kind = _KIND_LABELS.get(draft["kind"], draft["kind"].upper())
    if draft["kind"] in ("text", "rich"):
        return f"◆ {kind}\n{helpers.truncate(draft['text'], 500)}"
    return f"◆ {kind}\n‣ Caption: {helpers.truncate(draft.get('caption') or '-', 200)}"


def _retry_after(exc):
    """Telegram flood-wait seconds, capped so a broadcast cannot stall."""
    payload = getattr(exc, "result_json", None) or {}
    retry = (payload.get("parameters") or {}).get("retry_after")
    try:
        return min(int(retry), 10)
    except (TypeError, ValueError):
        return 0


def _capturing(message):
    user = message.from_user
    if user is None or not helpers.is_owner(user.id):
        return False
    if (message.text or "").startswith("/"):
        return False
    return state.current_flow(user.id) == "broadcast_content"


def _user_id(message):
    return message.from_user.id if message.from_user else None
