"""Turso / SQLite data access layer.

Two interchangeable backends sit behind one tiny interface:

* ``http(s)://`` (and ``libsql://``, rewritten to HTTPS) talks to Turso's
  Hrana HTTP API with ``urllib``.
* anything else (``file:sync.db``, ``local.db``, a plain path) is a local
  SQLite file through the standard-library ``sqlite3`` module, so the bot can
  be run locally without Turso credentials.

The Turso client is hand rolled because the official ``libsql-client`` HTTP
client assumes every reply carries a ``"result"`` key. Turso reports SQL
errors as **HTTP 200** with ``{"message": ..., "code": ...}``; the driver then
raised ``KeyError: 'result'``, which is what made ``init_db`` fail on a fresh
database. Here such a reply raises :class:`DatabaseError` with the real
message instead.

pyTelegramBotAPI polling handlers are synchronous. A module lock serialises
statements because the health-check thread and the polling thread share the
single connection. Every public function returns plain Python values (dicts,
ints, bools) so handlers never see driver objects.
"""

import base64
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import config

log = logging.getLogger(__name__)

_client = None
_lock = threading.RLock()

HTTP_TIMEOUT = 30


class DatabaseError(RuntimeError):
    """A statement rejected by the database, with Turso's code kept intact."""

    def __init__(self, message, code="UNKNOWN"):
        super().__init__(f"{message} [{code}]")
        self.message = message
        self.code = code


class ResultSet:
    """The small slice of a driver result this module actually uses."""

    __slots__ = ("rows", "rows_affected")

    def __init__(self, rows=(), rows_affected=0):
        self.rows = list(rows)
        self.rows_affected = rows_affected


def _encode_value(value):
    """Python value -> Hrana argument object."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "value": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _decode_value(cell):
    """Hrana value object -> plain Python value."""
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "blob":
        return base64.b64decode(cell["value"])
    return cell.get("value")


class _HttpConnection:
    """Turso Hrana HTTP API via urllib (one request per statement)."""

    def __init__(self, url, auth_token):
        self.url = url.rstrip("/") + "/v1/execute"
        self.auth_token = auth_token or ""

    def execute(self, sql, params=None):
        payload = json.dumps(
            {
                "stmt": {
                    "sql": sql,
                    "args": [_encode_value(value) for value in (params or [])],
                    "want_rows": True,
                }
            }
        ).encode()
        request = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            raise DatabaseError(_error_text(exc.read()) or f"HTTP {exc.code}", "HTTP_ERROR") from None
        except urllib.error.URLError as exc:
            raise DatabaseError(f"cannot reach Turso: {exc.reason}", "CONNECTION_FAILED") from None
        return _parse_result(body)

    def close(self):
        """Nothing to release: urllib opens a connection per request."""


class _SqliteConnection:
    """Local SQLite file, used for development without Turso."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.isolation_level = None  # autocommit, like the Turso backend

    def execute(self, sql, params=None):
        try:
            cursor = self.conn.execute(sql, tuple(params or ()))
            rows = cursor.fetchall() if cursor.description else []
            affected = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        except sqlite3.Error as exc:
            raise DatabaseError(str(exc), "SQLITE_ERROR") from None
        return ResultSet(rows, affected)

    def close(self):
        self.conn.close()


def _error_text(raw):
    """Best-effort message from an error body (JSON or plain text)."""
    if not raw:
        return ""
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return raw.decode("utf-8", "replace")[:300]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or "")
    return str(body)[:300]


def _parse_result(body):
    """Turso answer -> ResultSet, raising the real SQL error when there is one."""
    if not isinstance(body, dict):
        raise DatabaseError("unexpected Turso response", "PROTOCOL")
    if "result" not in body:
        message = body.get("message") or body.get("error") or "Turso rejected the statement"
        raise DatabaseError(str(message), str(body.get("code") or "UNKNOWN"))
    result = body.get("result") or {}
    rows = [tuple(_decode_value(cell) for cell in row) for row in (result.get("rows") or [])]
    return ResultSet(rows, int(result.get("affected_row_count") or 0))


def _turso_url(url):
    """Accept whatever URL the Turso dashboard hands out; talk HTTPS."""
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            return "https://" + url[len(prefix):]
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _sqlite_path(url):
    """``file:``/plain paths -> filesystem path for sqlite3."""
    path = (url or "local.db").strip()
    if path.startswith("file:"):
        path = path[len("file:"):]
        if path.startswith("///"):
            path = path[2:]
        elif path.startswith("//"):
            path = path[2:]
    return path or "local.db"

# Column order for groups, kept in sync with _SCHEMA below.
GROUP_FIELDS = (
    "group_id",
    "group_name",
    "added_by",
    "auto_approve",
    "welcome_dm",
    "log_channel",
    "account_age",
    "photo_required",
    "username_req",
    "min_age_days",
    "schedule_on",
    "schedule_start",
    "schedule_end",
    "anti_raid",
    "suspended",
    "custom_welcome",
    "added_at",
    "chat_type",
    "auto_decline",
    "accept_all",
    "skip_advanced",
)

MUTABLE_FIELDS = (
    "group_name",
    "accept_all",
    "skip_advanced",
    "auto_approve",
    "welcome_dm",
    "log_channel",
    "account_age",
    "photo_required",
    "username_req",
    "min_age_days",
    "schedule_on",
    "schedule_start",
    "schedule_end",
    "anti_raid",
    "auto_decline",
    "suspended",
    "custom_welcome",
)

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS groups (
        group_id        INTEGER PRIMARY KEY,
        group_name      TEXT,
        added_by        INTEGER,
        auto_approve    INTEGER DEFAULT 1,
        welcome_dm      INTEGER DEFAULT 1,
        log_channel     TEXT DEFAULT NULL,
        account_age     INTEGER DEFAULT 0,
        photo_required  INTEGER DEFAULT 0,
        username_req    INTEGER DEFAULT 0,
        min_age_days    INTEGER DEFAULT 30,
        schedule_on     INTEGER DEFAULT 0,
        schedule_start  INTEGER DEFAULT 9,
        schedule_end    INTEGER DEFAULT 21,
        anti_raid       INTEGER DEFAULT 0,
        suspended       INTEGER DEFAULT 0,
        custom_welcome  TEXT DEFAULT NULL,
        added_at        INTEGER,
        chat_type       TEXT DEFAULT 'group',
        auto_decline    INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY,
        first_name      TEXT,
        username        TEXT,
        started_bot     INTEGER DEFAULT 0,
        banned          INTEGER DEFAULT 0,
        joined_at       INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approved_members (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER,
        group_id        INTEGER,
        approved_at     INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS broadcast_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        sent_by         INTEGER,
        message         TEXT,
        sent_at         INTEGER,
        success_count   INTEGER,
        fail_count      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS banned_groups (
        group_id        INTEGER PRIMARY KEY,
        banned_by       INTEGER,
        reason          TEXT,
        banned_at       INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS required_channels (
        channel_id      INTEGER PRIMARY KEY,
        title           TEXT,
        invite_link     TEXT,
        added_at        INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_settings (
        key             TEXT PRIMARY KEY,
        value           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_approved_group ON approved_members (group_id)",
    "CREATE INDEX IF NOT EXISTS idx_approved_user ON approved_members (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_started ON users (started_bot)",
)

# Applied to databases created before a column existed. Duplicate-column errors
# mean the migration already ran and are ignored.
_MIGRATIONS = (
    "ALTER TABLE groups ADD COLUMN chat_type TEXT DEFAULT 'group'",
    "ALTER TABLE groups ADD COLUMN auto_decline INTEGER DEFAULT 0",
)


def _db():
    """The single shared connection, created on first use."""
    global _client
    if _client is None:
        url = (config.TURSO_DATABASE_URL or "").strip()
        if url.startswith(("http://", "https://", "libsql://", "ws://", "wss://")):
            target = _turso_url(url)
            if not config.TURSO_AUTH_TOKEN:
                raise DatabaseError(
                    "TURSO_AUTH_TOKEN is empty; Turso needs a database token",
                    "AUTH_REQUIRED",
                )
            _client = _HttpConnection(target, config.TURSO_AUTH_TOKEN)
            log.info("connected to Turso over HTTPS at %s", target)
        else:
            path = _sqlite_path(url)
            _client = _SqliteConnection(path)
            log.info("connected to local SQLite at %s", path)
    return _client


def _execute(sql, params=None):
    with _lock:
        try:
            return _db().execute(sql, params or [])
        except DatabaseError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface a readable error
            raise DatabaseError(f"{type(exc).__name__}: {exc}", "CLIENT_ERROR") from None


def _fetch(sql, params=None):
    return list(_execute(sql, params).rows)


def _row(row, fields):
    return dict(zip(fields, tuple(row)))


def close_db():
    """Close the shared connection (used on shutdown)."""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            finally:
                _client = None


def _columns(table):
    """Column names of ``table`` (empty when the table does not exist yet)."""
    try:
        return {tuple(row)[1] for row in _fetch(f"PRAGMA table_info({table})")}
    except DatabaseError:
        return set()


def init_db():
    """Create every table and index if missing, then apply migrations."""
    with _lock:
        for statement in _SCHEMA:
            _db().execute(statement, [])
        for statement in _MIGRATIONS:
            column = statement.split("ADD COLUMN", 1)[-1].split()[0]
            if column in _columns("groups"):
                continue
            try:
                _db().execute(statement, [])
            except DatabaseError as exc:  # another worker won the race
                if "duplicate column" not in str(exc).lower():
                    raise
    log.info("database schema ready")


# ── Groups ───────────────────────────────────────────────────

def get_group(group_id):
    """Return the group row as a dict, or None."""
    rows = _fetch(
        "SELECT " + ", ".join(GROUP_FIELDS) + " FROM groups WHERE group_id = ?",
        [int(group_id)],
    )
    return _row(rows[0], GROUP_FIELDS) if rows else None


def get_or_add_group(group_id, group_name, added_by=None, chat_type="group"):
    """Fetch a chat row, inserting it first when the bot has not seen it."""
    row = get_group(group_id)
    if row is None:
        add_group(group_id, group_name, added_by, chat_type)
        row = get_group(group_id)
    return row


def add_group(group_id, group_name, added_by, chat_type="group"):
    """Insert a chat with all defaults (existing rows keep their settings).

    `chat_type` is 'group', 'supergroup' or 'channel'; channels are tracked so
    admins can open a settings panel for them from private chat.
    """
    _execute(
        """
        INSERT INTO groups (group_id, group_name, added_by, added_at, chat_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET
            group_name = excluded.group_name,
            chat_type  = excluded.chat_type
        """,
        [int(group_id), group_name, added_by, int(time.time()), chat_type or "group"],
    )


def get_all_chats(limit=50):
    """Every group and channel the bot knows, newest first."""
    rows = _fetch(
        """
        SELECT group_id, group_name, chat_type, auto_approve, suspended, added_by, added_at
        FROM groups ORDER BY added_at DESC LIMIT ?
        """,
        [int(limit)],
    )
    fields = ("group_id", "group_name", "chat_type", "auto_approve", "suspended", "added_by", "added_at")
    return [_row(row, fields) for row in rows]


def update_group_setting(group_id, key, value):
    """Update one whitelisted settings column for a group."""
    if key not in MUTABLE_FIELDS:
        raise ValueError(f"unknown group setting: {key}")
    result = _execute(
        f"UPDATE groups SET {key} = ? WHERE group_id = ?",
        [value, int(group_id)],
    )
    return bool(getattr(result, "rows_affected", 0))


def get_group_setting(group_id, key):
    """Return a single setting value, or None when the group is unknown."""
    if key not in GROUP_FIELDS:
        raise ValueError(f"unknown group setting: {key}")
    rows = _fetch(f"SELECT {key} FROM groups WHERE group_id = ?", [int(group_id)])
    return tuple(rows[0])[0] if rows else None


def get_all_groups():
    """Every known group, newest first, for the owner's group list."""
    rows = _fetch(
        """
        SELECT group_id, group_name, auto_approve, suspended, added_at
        FROM groups ORDER BY added_at DESC
        """,
        [],
    )
    return [_row(row, ("group_id", "group_name", "auto_approve", "suspended", "added_at")) for row in rows]


def set_all_suspended(value):
    """Maintenance mode: suspend or resume every group at once."""
    _execute("UPDATE groups SET suspended = ?", [1 if value else 0])


# ── Users ────────────────────────────────────────────────────

def add_user(user_id, first_name, username):
    """Insert or refresh a user."""
    _execute(
        """
        INSERT INTO users (user_id, first_name, username, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            username   = excluded.username
        """,
        [int(user_id), first_name, username, int(time.time())],
    )


def get_all_started_users():
    """Every user who has started the bot and is not banned."""
    rows = _fetch("SELECT user_id FROM users WHERE started_bot = 1 AND banned = 0", [])
    return [int(tuple(row)[0]) for row in rows]


def mark_user_started(user_id):
    """Remember that a user can receive direct messages from the bot."""
    _execute("UPDATE users SET started_bot = 1 WHERE user_id = ?", [int(user_id)])


def ban_user(user_id):
    """Ban a user, creating the row if they never started the bot."""
    _execute(
        """
        INSERT INTO users (user_id, banned, joined_at) VALUES (?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET banned = 1
        """,
        [int(user_id), int(time.time())],
    )


def unban_user(user_id):
    _execute("UPDATE users SET banned = 0 WHERE user_id = ?", [int(user_id)])


def is_user_banned(user_id):
    rows = _fetch("SELECT banned FROM users WHERE user_id = ?", [int(user_id)])
    return bool(rows and tuple(rows[0])[0])


# ── Approvals ────────────────────────────────────────────────

def log_approval(user_id, group_id):
    _execute(
        "INSERT INTO approved_members (user_id, group_id, approved_at) VALUES (?, ?, ?)",
        [int(user_id), int(group_id), int(time.time())],
    )


def get_stats(group_id):
    """Number of members this bot approved in a group."""
    rows = _fetch("SELECT COUNT(*) FROM approved_members WHERE group_id = ?", [int(group_id)])
    return int(tuple(rows[0])[0]) if rows else 0


def get_last_approval(group_id):
    """Unix timestamp of the newest approval in a group, or None."""
    rows = _fetch(
        "SELECT approved_at FROM approved_members WHERE group_id = ? ORDER BY approved_at DESC LIMIT 1",
        [int(group_id)],
    )
    return int(tuple(rows[0])[0]) if rows else None


def get_global_stats():
    """Totals across the whole deployment: groups, users, approvals."""
    groups = _fetch("SELECT COUNT(*) FROM groups", [])
    users = _fetch("SELECT COUNT(*) FROM users", [])
    approved = _fetch("SELECT COUNT(*) FROM approved_members", [])
    return {
        "groups": int(tuple(groups[0])[0]) if groups else 0,
        "users": int(tuple(users[0])[0]) if users else 0,
        "approved": int(tuple(approved[0])[0]) if approved else 0,
    }


def get_recent_approvals(limit=5):
    """Most recent approvals for the owner's activity view."""
    rows = _fetch(
        """
        SELECT user_id, group_id, approved_at FROM approved_members
        ORDER BY approved_at DESC LIMIT ?
        """,
        [int(limit)],
    )
    return [_row(row, ("user_id", "group_id", "approved_at")) for row in rows]


# ── Bans ─────────────────────────────────────────────────────

def ban_group(group_id, banned_by, reason):
    _execute(
        """
        INSERT INTO banned_groups (group_id, banned_by, reason, banned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET
            banned_by = excluded.banned_by,
            reason    = excluded.reason,
            banned_at = excluded.banned_at
        """,
        [int(group_id), banned_by, reason, int(time.time())],
    )


def unban_group(group_id):
    result = _execute("DELETE FROM banned_groups WHERE group_id = ?", [int(group_id)])
    return bool(getattr(result, "rows_affected", 0))


def is_group_banned(group_id):
    rows = _fetch("SELECT 1 FROM banned_groups WHERE group_id = ?", [int(group_id)])
    return bool(rows)


# ── Bot settings (verify text, media links) ──────────────────

def get_setting(key, default=None):
    """One global setting value, or `default` when it was never set."""
    rows = _fetch("SELECT value FROM bot_settings WHERE key = ?", [str(key)])
    if not rows:
        return default
    value = tuple(rows[0])[0]
    return value if value is not None else default


def set_setting(key, value):
    """Insert or replace one global setting."""
    _execute(
        """
        INSERT INTO bot_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [str(key), None if value is None else str(value)],
    )


def delete_setting(key):
    """Drop one setting so its fallback takes over again."""
    _execute("DELETE FROM bot_settings WHERE key = ?", [str(key)])


# ── Required channels (force join) ───────────────────────────

def get_required_channels():
    """Every channel a user must join before the bot answers them."""
    fields = ("channel_id", "title", "invite_link", "added_at")
    rows = _fetch(
        "SELECT " + ", ".join(fields) + " FROM required_channels ORDER BY added_at ASC, channel_id ASC",
        [],
    )
    return [_row(row, fields) for row in rows]


def add_required_channel(channel_id, title, invite_link):
    """Register a channel, refreshing its title and invite link."""
    _execute(
        """
        INSERT INTO required_channels (channel_id, title, invite_link, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title       = excluded.title,
            invite_link = excluded.invite_link
        """,
        [int(channel_id), title, invite_link, int(time.time())],
    )


def remove_required_channel(channel_id):
    """Remove a channel from the force-join list."""
    result = _execute("DELETE FROM required_channels WHERE channel_id = ?", [int(channel_id)])
    return bool(getattr(result, "rows_affected", 0))


def get_user(user_id):
    """Stored profile for a user, or None."""
    fields = ("user_id", "first_name", "username", "started_bot", "banned", "joined_at")
    rows = _fetch("SELECT " + ", ".join(fields) + " FROM users WHERE user_id = ?", [int(user_id)])
    return _row(rows[0], fields) if rows else None


# ── Broadcasts ───────────────────────────────────────────────

def log_broadcast(sent_by, message, success_count, fail_count):
    _execute(
        """
        INSERT INTO broadcast_log (sent_by, message, sent_at, success_count, fail_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [sent_by, message, int(time.time()), int(success_count), int(fail_count)],
    )
