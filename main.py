"""Nyro X Auto Approve Bot entry point.

Runs long polling on the main thread and a small Flask health endpoint on a
daemon thread, which is what keeps a free Render web service alive when Uptime
Robot pings it every few minutes.
"""

import logging
import threading
import time

import telebot
from flask import Flask

import config
from database import turso
from handlers import register_all
from utils.gate import JoinGate

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nyro")

bot = telebot.TeleBot(
    config.BOT_TOKEN,
    parse_mode=None,
    threaded=False,
    # The force-join gate is a class middleware, so handlers keep their order
    # and non-members are stopped before any of them run.
    use_class_middlewares=True,
)

app = Flask(__name__)


@app.get("/")
def health():
    return "◆ Nyro X Auto Approve — Online", 200


def _run_health_server():
    app.run(host="0.0.0.0", port=config.HEALTH_PORT, threaded=True, use_reloader=False)


def _publish_commands():
    commands = [
        ("start", "Main menu or owner panel"),
        ("settings", "Open the settings menu"),
        ("stats", "Approval statistics"),
        ("approveall", "Approve every pending request"),
        ("declineall", "Decline every pending request"),
        ("setwelcome", "Set a custom welcome message"),
        ("resetwelcome", "Reset to the Nyro X welcome"),
        ("suspend", "Suspend the bot in this group"),
        ("resume", "Resume the bot in this group"),
        ("cancel", "Cancel the current prompt"),
        ("help", "List every command"),
        ("owner", "Owner panel"),
        ("admin", "Admin panel"),
        ("broadcast", "Start a broadcast"),
        ("bangroup", "Ban a group"),
        ("banuser", "Ban a user"),
        ("unbangroup", "Unban a group"),
        ("unbanuser", "Unban a user"),
        ("groups", "List active groups"),
        ("globalstats", "Global statistics"),
    ]
    try:
        bot.set_my_commands([telebot.types.BotCommand(name, description) for name, description in commands])
    except Exception:  # noqa: BLE001 - a missing command list is not fatal
        log.warning("could not publish the command list", exc_info=True)


def _connect_database(attempts=5, delay=5):
    """Turso can be slow to answer while a Render instance is cold-starting."""
    for attempt in range(1, attempts + 1):
        try:
            turso.init_db()
            return
        except Exception:  # noqa: BLE001 - retried below
            if attempt == attempts:
                raise
            log.warning("database not ready (attempt %s/%s), retrying in %ss", attempt, attempts, delay)
            time.sleep(delay)


def _poll_forever():
    """Long polling with reconnect.

    A Bot API outage, a revoked-then-restored token, or a competing
    getUpdates consumer all surface as exceptions; none of them should take
    the bot offline, so a failed poll is retried after a short pause.
    """
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=False)
        except Exception:  # noqa: BLE001 - stay online across transient failures
            log.exception("polling stopped; retrying in 5s")
            time.sleep(5)


def main():
    _connect_database()
    register_all(bot)
    bot.setup_middleware(JoinGate(bot))

    threading.Thread(target=_run_health_server, name="health", daemon=True).start()
    # Publishing the command list is cosmetic: a slow or failing Telegram call
    # must never delay long polling, so it runs on its own daemon thread.
    threading.Thread(target=_publish_commands, name="commands", daemon=True).start()

    log.info("%s v%s online — health check on :%s", config.BOT_NAME, config.VERSION, config.HEALTH_PORT)
    try:
        _poll_forever()
    except KeyboardInterrupt:
        log.info("stopped by operator")
    finally:
        turso.close_db()


if __name__ == "__main__":
    main()
