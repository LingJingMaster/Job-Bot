"""SQLite 连接与可重复迁移。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jobbot.contracts import JobBotError

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATION_ID = "001_init"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid4().hex


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    """执行 schema.sql。重复调用不会删除已有数据。"""
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if foreign_keys != 1:
        raise JobBotError("连接未打开 foreign_keys。下一步：使用 jobbot.db.connection.connect。")
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    connection.executescript(script)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration (id, applied_at) VALUES (?, ?)",
        (MIGRATION_ID, utc_now_iso()),
    )
    connection.commit()
