"""Copy local sync.db rows into the remote Turso database.

Run with TURSO_DATABASE_URL / TURSO_AUTH_TOKEN pointing at the remote database.
Rows are upserted on their primary key, so re-running is safe.
"""

import os
import sqlite3
import sys

LOCAL = os.environ.get("SYNC_SOURCE", "sync.db")

sys.path.insert(0, ".venv/lib/python3.12/site-packages")
sys.path.insert(0, ".")

from database import turso  # noqa: E402

SKIP = {"sqlite_sequence"}


def local_tables(conn):
    return [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if row[0] not in SKIP and not row[0].startswith("__turso")
    ]


def table_info(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = [row[1] for row in rows]
    keys = [row[1] for row in sorted(rows, key=lambda r: r[5]) if row[5]]
    return columns, keys


def sync_table(conn, table):
    columns, keys = table_info(conn, table)
    rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    if not rows:
        print(f"{table}: nothing to copy")
        return 0

    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in keys)
    if keys and updates:
        conflict = f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}"
    elif keys:
        conflict = f"ON CONFLICT({', '.join(keys)}) DO NOTHING"
    else:
        conflict = ""
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) {conflict}"

    for row in rows:
        turso._execute(sql, list(row))
    print(f"{table}: {len(rows)} row(s) copied")
    return len(rows)


def main():
    turso.init_db()
    conn = sqlite3.connect(f"file:{LOCAL}?mode=ro", uri=True)
    total = 0
    for table in local_tables(conn):
        total += sync_table(conn, table)
    conn.close()

    print("\nremote verification:")
    print("  bot_settings:", dict((r[0], r[1][:28] + ("..." if r[1] and len(r[1]) > 28 else "")) for r in turso._fetch("SELECT key, value FROM bot_settings")))
    print("  required_channels:", turso.get_required_channels())
    print("  users:", turso._fetch("SELECT COUNT(*) FROM users")[0][0],
          "| groups:", turso._fetch("SELECT COUNT(*) FROM groups")[0][0])
    print(f"\n{total} row(s) synced")


if __name__ == "__main__":
    main()
