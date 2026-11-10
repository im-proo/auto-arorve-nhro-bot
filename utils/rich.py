"""Rich message transport.

Bot API 10.x sends rich messages with `sendRichMessage` and edits them with
`editMessageText(rich_message=...)`. pyTelegramBotAPI 4.37+ exposes both.
A deployment can still meet an endpoint or client that refuses a rich payload
(for example a media block it cannot resolve), so every send here degrades to
a plain HTML message rather than losing the screen entirely.
"""

import logging
import re

from telebot import types

from utils import helpers

log = logging.getLogger(__name__)

DIVIDER = "─────────────"


def _markup(keyboard):
    """Accept a ui.Keyboard, a raw InlineKeyboardMarkup, or None."""
    if keyboard is None:
        return None
    builder = getattr(keyboard, "to_markup", None)
    return builder() if callable(builder) else keyboard


def build(html, media=None):
    """Build the InputRichMessage for a rich screen.

    Screens that embed the shared menu video reference it with a tg://video
    link; the matching attachment is added here so every send and edit keeps
    the video without each call site having to remember it.
    """
    attachments = list(media or [])
    from utils import ui  # local import: ui builds the HTML this transport sends

    html = ui.hoist_video(html)
    if ui.MENU_VIDEO_ID in html and not any(
        getattr(item, "id", None) == ui.MENU_VIDEO_ID for item in attachments
    ):
        attachments.extend(ui.menu_video_media())
    return types.InputRichMessage(html=html, media=attachments or None)


def to_plain(html):
    """Downgrade rich HTML to the tags plain sendMessage understands."""
    text = re.sub(r"<tg-slideshow.*?</tg-slideshow>", "", html, flags=re.S | re.I)
    text = re.sub(r"<blockquote>\s*<video.*?</video>\s*</blockquote>", "", text, flags=re.S | re.I)
    text = re.sub(r"<figure>\s*<video.*?</video>.*?</figure>", "", text, flags=re.S | re.I)
    text = re.sub(r"<video[^>]*>\s*</video>", "", text, flags=re.I)
    text = re.sub(r"<blockquote>\s*</blockquote>", "", text, flags=re.I)
    text = re.sub(r"<(h[1-6])[^>]*>(.*?)</\1>", r"<b>\2</b>\n\n", text, flags=re.S | re.I)
    text = re.sub(r"<hr\s*/?>", f"\n{DIVIDER}\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", text, flags=re.I)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?figcaption[^>]*>", "", text, flags=re.I)
    text = re.sub(r"</?(?:figure|footer)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<cite[^>]*>(.*?)</cite>", r"\1", text, flags=re.S | re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return helpers.truncate(text.strip(), 3900)


def send_rich(bot, chat_id, html, keyboard=None, media=None, silent=False):
    """Send a rich screen, falling back to plain HTML if the API refuses it."""
    try:
        return bot.send_rich_message(
            chat_id=chat_id,
            rich_message=build(html, media),
            reply_markup=_markup(keyboard),
            disable_notification=silent or None,
        )
    except Exception as exc:  # noqa: BLE001 - fallback is the point of this layer
        log.warning("rich send to %s failed (%s); retrying as plain HTML", chat_id, exc)

    try:
        return bot.send_message(
            chat_id=chat_id,
            text=to_plain(html),
            parse_mode="HTML",
            reply_markup=_markup(keyboard),
            disable_web_page_preview=True,
        )
    except Exception:  # noqa: BLE001 - the caller cannot do anything else
        log.exception("plain fallback send to %s failed", chat_id)
        return None


def edit_rich(bot, chat_id, message_id, html, keyboard=None):
    """Update an existing screen in place, rich first then plain HTML."""
    markup = _markup(keyboard)
    try:
        return bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            rich_message=build(html),
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001 - fallback is the point of this layer
        if _not_modified(exc):
            return None
        log.warning("rich edit of %s/%s failed (%s); retrying as plain HTML", chat_id, message_id, exc)

    try:
        return bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=to_plain(html),
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001
        if not _not_modified(exc):
            log.exception("plain fallback edit of %s/%s failed", chat_id, message_id)
        return None


def send_plain(bot, chat_id, text):
    """Plain sendMessage: log channels, delivery reports, short notices."""
    try:
        return bot.send_message(chat_id=chat_id, text=helpers.truncate(text, 4000), disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        log.warning("plain send to %s failed", chat_id, exc_info=True)
        return None


def answer(bot, call, text=None, alert=False):
    """Answer a callback query; never let a stale query break a handler."""
    try:
        bot.answer_callback_query(call.id, text=text or "", show_alert=alert)
    except Exception:  # noqa: BLE001
        log.debug("could not answer callback %s", getattr(call, "id", "?"))


def _not_modified(exc):
    return "message is not modified" in str(exc).lower()
