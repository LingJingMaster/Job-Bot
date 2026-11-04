"""按 id 插入、读取和列出。只额外强制两条不可变约束。"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from jobbot.contracts import JobBotError
from jobbot.db.connection import new_id, utc_now_iso

TABLES = frozenset(
    {
        "document",
        "profile",
        "fact",
        "profile_snapshot",
        "preference",
        "collect_run",
        "collect_command",
        "job",
        "job_snapshot",
        "jd_parse",
        "match_run",
        "match_result",
        "label",
        "feedback",
        "material",
        "claim",
        "question",
        "application",
        "application_event",
        "llm_call",
        "experiment_run",
    }
)

JOB_SNAPSHOT_CONTENT_FIELDS = frozenset(
    {
        "job_id",
        "run_id",
        "fetched_at",
        "raw_path",
        "content_hash",
        "title",
        "company",
        "city",
        "district",
        "salary_text",
        "salary_min",
        "salary_max",
        "salary_unit",
        "salary_months",
        "experience_text",
        "degree_text",
        "description",
        "hr_title",
        "hr_active_text",
        "availability",
        "source",
    }
)

_COLUMN_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _require_table(table: str) -> None:
    if table not in TABLES:
        raise JobBotError(f"未知表 {table}。数据未写入。")


def _encode(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> str:
    _require_table(table)
    payload = dict(row)
    now = utc_now_iso()
    if not payload.get("id"):
        payload["id"] = new_id()
    payload.setdefault("created_at", now)
    payload.setdefault("updated_at", now)
    columns = list(payload)
    for column in columns:
        if _COLUMN_NAME.fullmatch(column) is None:
            raise JobBotError(f"非法列名 {column}。数据未写入。")
        payload[column] = _encode(payload[column])
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    try:
        connection.execute(sql, [payload[column] for column in columns])
        connection.commit()
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "sha256" in message:
            raise JobBotError("相同 sha256 的文件已存在。原文件未覆盖。下一步：使用已有 document。") from exc
        raise JobBotError(f"写入 {table} 被拒绝：{message}。数据未写入。") from exc
    except sqlite3.OperationalError as exc:
        raise JobBotError(f"写入 {table} 失败：{exc}。数据未写入。") from exc
    return str(payload["id"])


def get_by_id(connection: sqlite3.Connection, table: str, row_id: str) -> dict[str, Any] | None:
    _require_table(table)
    cursor = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def list_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    _require_table(table)
    cursor = connection.execute(f"SELECT * FROM {table} ORDER BY created_at, id")
    return [dict(row) for row in cursor.fetchall()]


def update_by_id(
    connection: sqlite3.Connection,
    table: str,
    row_id: str,
    fields: dict[str, Any],
) -> None:
    _require_table(table)
    if not fields:
        raise JobBotError("没有要更新的字段。数据未修改。")
    current = get_by_id(connection, table, row_id)
    if current is None:
        raise JobBotError(f"{table} 中不存在 id={row_id}。数据未修改。")
    if table == "job_snapshot":
        blocked = sorted(set(fields) & JOB_SNAPSHOT_CONTENT_FIELDS)
        if blocked:
            raise JobBotError(
                "job_snapshot 正文字段不可更新："
                + "、".join(blocked)
                + "。已有快照未修改。下一步：写入新的 job_snapshot。"
            )
    if table == "material" and ({"content", "content_hash"} & set(fields)):
        if current["status"] == "approved":
            raise JobBotError(
                "material 在 status=approved 后不可修改 content。"
                "已批准正文未修改。下一步：新建一条 revision。"
            )
    payload = dict(fields)
    payload["updated_at"] = utc_now_iso()
    assignments: list[str] = []
    values: list[Any] = []
    for column, value in payload.items():
        if _COLUMN_NAME.fullmatch(column) is None:
            raise JobBotError(f"非法列名 {column}。数据未修改。")
        assignments.append(f"{column} = ?")
        values.append(_encode(value))
    values.append(row_id)
    sql = f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?"
    try:
        connection.execute(sql, values)
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise JobBotError(f"更新 {table} 被拒绝：{exc}。数据未修改。") from exc
