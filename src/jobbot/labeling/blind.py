"""待标岗位。只准备 JD 原文、已确认事实和偏好，不读取排序结果。"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jobbot.db.repository import list_rows
from jobbot.labeling.db import loads_json

_FIELDS = {
    "city": "城市",
    "degree": "学历",
    "experience_years": "经验年限",
    "salary_min": "薪资下限",
    "salary_max": "薪资上限",
    "keyword_exclude": "排除词",
}
_OPERATORS = {
    "eq": "等于",
    "in": "属于",
    "gte": "不低于",
    "lte": "不高于",
    "not_contains": "不包含",
}


@dataclass(frozen=True)
class BlindJob:
    snapshot_id: str
    jd_text: str


def has_job_snapshots(connection: sqlite3.Connection) -> bool:
    return bool(list_rows(connection, "job_snapshot"))


def list_profile_choices(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for row in list_rows(connection, "profile"):
        name = str(row["name"]).strip() if row.get("name") else ""
        choices.append((str(row["id"]), name or "未命名档案"))
    return choices


def list_pending_jobs(
    connection: sqlite3.Connection,
    profile_id: str,
    labeler: str,
    round_name: str,
) -> list[BlindJob]:
    labeled = {
        str(row["snapshot_id"])
        for row in list_rows(connection, "label")
        if row["profile_id"] == profile_id
        and row["labeler"] == labeler
        and row["round"] == round_name
    }
    pending = [
        BlindJob(snapshot_id=str(row["id"]), jd_text=str(row["description"] or ""))
        for row in list_rows(connection, "job_snapshot")
        if str(row["id"]) not in labeled
    ]
    pending.sort(key=lambda job: job.snapshot_id)
    return pending


def shuffled_ids(snapshot_ids: Sequence[str], seed: int) -> list[str]:
    ordered = [str(item) for item in snapshot_ids]
    random.Random(seed).shuffle(ordered)
    return ordered


def ordered_jobs(jobs: Sequence[BlindJob], seed: int) -> list[BlindJob]:
    by_id = {job.snapshot_id: job for job in jobs}
    ordered_ids = shuffled_ids([job.snapshot_id for job in jobs], seed)
    return [by_id[snapshot_id] for snapshot_id in ordered_ids]


def confirmed_statements(connection: sqlite3.Connection, profile_id: str) -> list[str]:
    latest: dict[str, dict[str, Any]] = {}
    for row in list_rows(connection, "fact"):
        if row["profile_id"] != profile_id:
            continue
        key = str(row["fact_key"])
        current = latest.get(key)
        if current is None or int(row["revision"]) >= int(current["revision"]):
            latest[key] = row
    statements = [
        str(row["statement"])
        for row in latest.values()
        if row["status"] == "confirmed"
    ]
    statements.sort()
    return statements


def preference_lines(connection: sqlite3.Connection, profile_id: str) -> list[str]:
    rows = [row for row in list_rows(connection, "preference") if row["profile_id"] == profile_id]
    if not rows:
        return ["还没有偏好。"]
    current = max(rows, key=lambda row: int(row["revision"]))
    return [
        _hard_line(loads_json(current.get("hard"))),
        _soft_line(loads_json(current.get("soft"))),
        _terms_line(loads_json(current.get("search_terms"))),
    ]


def _hard_line(hard: Any) -> str:
    if hard is None:
        return "硬条件未知。"
    if hard == []:
        return "硬条件：确认没有。"
    if not isinstance(hard, list):
        return f"硬条件：{hard}"
    parts = [_constraint_text(item) for item in hard]
    return "硬条件：" + "；".join(parts)


def _constraint_text(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    field = _FIELDS.get(str(item.get("field")), str(item.get("field")))
    operator = _OPERATORS.get(str(item.get("operator")), str(item.get("operator")))
    return f"{field} {operator} {_value_text(item.get('value'))}"


def _soft_line(soft: Any) -> str:
    if soft is None:
        return "软偏好未知。"
    if soft == []:
        return "软偏好：确认没有。"
    return "软偏好：" + _value_text(soft)


def _terms_line(terms: Any) -> str:
    if terms is None:
        return "搜索词未知。"
    if terms == []:
        return "搜索词：确认没有。"
    return "搜索词：" + _value_text(terms)


def _value_text(value: Any) -> str:
    if value is None:
        return "未知"
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, dict):
                chunks.append("，".join(f"{key}={item[key]}" for key in item))
            else:
                chunks.append(str(item))
        return "、".join(chunks)
    if isinstance(value, dict):
        return "，".join(f"{key}={value[key]}" for key in value)
    return str(value)
