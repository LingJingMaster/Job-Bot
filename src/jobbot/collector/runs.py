"""采集运行与停止命令。界面写命令，采集进程读 collect_run。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from jobbot import __version__
from jobbot.contracts import CollectLimits, JobBotError
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id

from jobbot.collector.database import open_database


def start_run(
    mode: Literal["assist", "auto"],
    keyword: str | None,
    limits: CollectLimits,
    *,
    connection: sqlite3.Connection | None = None,
) -> str:
    if mode not in ("assist", "auto"):
        raise JobBotError(
            "采集模式只能是 assist 或 auto。没有创建运行记录。",
            saved=False,
            next_step="改用辅助模式后再启动。",
        )
    params = {
        "max_jobs": limits.max_jobs,
        "max_minutes": limits.max_minutes,
        "consecutive_parse_failures": 0,
    }
    with open_database(connection) as db:
        run_id = insert(
            db,
            "collect_run",
            {
                "mode": mode,
                "keyword": keyword,
                "city": None,
                "params": params,
                "status": "running",
                "stop_reason": None,
                "started_at": utc_now_iso(),
                "finished_at": None,
                "n_seen": 0,
                "n_ok": 0,
                "n_fail": 0,
                "collector_version": __version__,
            },
        )
        insert(
            db,
            "collect_command",
            {
                "run_id": run_id,
                "command": "start",
                "payload": params,
                "handled_at": None,
            },
        )
        return run_id


def stop_run(run_id: str, *, connection: sqlite3.Connection | None = None) -> None:
    with open_database(connection) as db:
        row = get_by_id(db, "collect_run", run_id)
        if row is None:
            raise JobBotError(
                f"没有编号为 {run_id} 的采集。没有写入停止命令。",
                saved=False,
                next_step="先启动采集。",
            )
        if row["status"] != "running":
            raise JobBotError(
                "这次采集已经结束。没有再写一条停止命令。已保存的数据还在。",
                saved=True,
                next_step="查看本条记录的停止原因。",
            )
        insert(
            db,
            "collect_command",
            {
                "run_id": run_id,
                "command": "stop",
                "payload": {},
                "handled_at": None,
            },
        )


def read_params(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded
    return {}


def pending_user_stop(connection: sqlite3.Connection, run_id: str) -> bool:
    for row in list_rows(connection, "collect_command"):
        if row["run_id"] == run_id and row["command"] == "stop" and row["handled_at"] is None:
            return True
    return False


def mark_commands_handled(connection: sqlite3.Connection, run_id: str) -> None:
    now = utc_now_iso()
    for row in list_rows(connection, "collect_command"):
        if row["run_id"] == run_id and row["handled_at"] is None:
            update_by_id(connection, "collect_command", row["id"], {"handled_at": now})


def write_run_progress(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    n_seen: int,
    n_ok: int,
    n_fail: int,
    consecutive_parse_failures: int,
    stop_reason: str | None,
    status: str | None = None,
) -> None:
    current = get_by_id(connection, "collect_run", run_id)
    if current is None:
        raise JobBotError(
            f"没有编号为 {run_id} 的采集，进度没有写入。",
            saved=False,
            next_step="重新启动一次采集。",
        )
    params = read_params(current["params"])
    params["consecutive_parse_failures"] = consecutive_parse_failures
    fields: dict[str, Any] = {
        "n_seen": n_seen,
        "n_ok": n_ok,
        "n_fail": n_fail,
        "params": params,
    }
    if stop_reason:
        fields["stop_reason"] = stop_reason
        fields["status"] = status or "stopped"
        fields["finished_at"] = utc_now_iso()
        mark_commands_handled(connection, run_id)
    update_by_id(connection, "collect_run", run_id, fields)
