"""岗位模块使用的数据库连接。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from jobbot.config import load_config
from jobbot.db.connection import connect, migrate


def loads_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    settings = load_config()
    connection = connect(settings.data_dir / "db" / "jobbot.sqlite")
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()
