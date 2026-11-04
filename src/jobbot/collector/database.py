"""采集器使用的数据库连接。路径与 LLM 网关相同：DATA_DIR/db/jobbot.sqlite。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from jobbot.config import load_config
from jobbot.db.connection import connect, migrate


def database_path() -> Path:
    return load_config().data_dir / "db" / "jobbot.sqlite"


@contextmanager
def open_database(connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    if connection is not None:
        yield connection
        return
    owned = connect(database_path())
    try:
        migrate(owned)
        yield owned
    finally:
        owned.close()
