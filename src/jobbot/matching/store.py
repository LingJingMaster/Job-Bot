"""读取档案、岗位和标注，并写入 match_run。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from jobbot.config import load_config
from jobbot.contracts import (
    Fact,
    FactStructured,
    JDParse,
    JobBotError,
    JobSnapshot,
    MatchResult,
    MatchRun,
    Preference,
    ProfileSnapshot,
    Requirement,
    SourceRef,
)
from jobbot.db.connection import connect, migrate, new_id
from jobbot.db.repository import get_by_id, insert, list_rows
from jobbot.profile.preference import latest_preference


def new_run_id() -> str:
    return new_id()


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    settings = load_config()
    connection = connect(settings.data_dir / "db" / "jobbot.sqlite")
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()


def profile_name(profile_id: str) -> str | None:
    with open_db() as connection:
        row = get_by_id(connection, "profile", profile_id)
    if row is None:
        return None
    name = row.get("name")
    return str(name) if name else None


def load_snapshot_facts(snapshot: ProfileSnapshot) -> list[Fact]:
    with open_db() as connection:
        stored = get_by_id(connection, "profile_snapshot", snapshot.id)
        if stored is None:
            raise JobBotError(
                "档案快照不在库里。没有排序。",
                saved=False,
                next_step="使用 freeze_profile 生成的快照。",
            )
        facts: list[Fact] = []
        for fact_id in snapshot.fact_ids:
            row = get_by_id(connection, "fact", fact_id)
            if row is None:
                raise JobBotError(
                    f"档案快照引用的事实 {fact_id} 不存在。没有排序。",
                    saved=False,
                    next_step="重新冻结只含已确认事实的档案。",
                )
            facts.append(_fact_from_row(row))
    rejected = [fact.id or "" for fact in facts if fact.status != "confirmed"]
    if rejected:
        raise JobBotError(
            "档案快照含有未确认事实。rank 只接受确认快照，没有排序。",
            saved=False,
            next_step="先确认事实，再调用 freeze_profile。",
        )
    return facts


def require_preference(pref: Preference) -> str:
    if not pref.id:
        raise JobBotError(
            "偏好还没有入库，没有 id。没有排序。",
            saved=False,
            next_step="先保存偏好。",
        )
    with open_db() as connection:
        row = get_by_id(connection, "preference", pref.id)
    if row is None:
        raise JobBotError(
            "偏好 id 在库里不存在。没有排序。",
            saved=False,
            next_step="重新保存偏好后再排序。",
        )
    return pref.id


def list_profiles() -> list[dict[str, Any]]:
    with open_db() as connection:
        return list_rows(connection, "profile")


def list_profile_snapshots(profile_id: str) -> list[ProfileSnapshot]:
    from jobbot.profile import list_snapshots

    return list_snapshots(profile_id)


def get_job_snapshot(snapshot_id: str) -> JobSnapshot:
    with open_db() as connection:
        row = get_by_id(connection, "job_snapshot", snapshot_id)
        if row is None:
            raise JobBotError(
                f"找不到岗位快照 {snapshot_id}。词表没有拟合。",
                saved=False,
                next_step="传入已经入库的岗位快照 id。",
            )
        job = get_by_id(connection, "job", str(row["job_id"]))
    if job is None:
        raise JobBotError("岗位快照没有对应岗位。词表没有拟合。", saved=False, next_step="重新入库该岗位。")
    return _snapshot_from_row(row, str(job["platform"]))


def list_job_snapshots() -> list[JobSnapshot]:
    with open_db() as connection:
        jobs = {row["id"]: row for row in list_rows(connection, "job")}
        rows = list_rows(connection, "job_snapshot")
    snapshots: list[JobSnapshot] = []
    for row in rows:
        job = jobs.get(row["job_id"])
        if job is None:
            continue
        snapshots.append(_snapshot_from_row(row, str(job["platform"])))
    return snapshots


def latest_preference_for(profile_id: str) -> Preference | None:
    return latest_preference(profile_id)


def latest_ok_parse(snapshot_id: str) -> JDParse | None:
    with open_db() as connection:
        rows = [
            row
            for row in list_rows(connection, "jd_parse")
            if row["snapshot_id"] == snapshot_id and row["status"] == "ok"
        ]
    if not rows:
        return None
    row = max(rows, key=lambda item: (str(item["created_at"]), str(item["id"])))
    return _parse_from_row(row)


def label_map(profile_id: str, extra: list[dict[str, Any]] | None) -> dict[str, int]:
    found: dict[str, int] = {}
    with open_db() as connection:
        for row in list_rows(connection, "label"):
            if row["profile_id"] != profile_id or row["rating"] is None:
                continue
            found[str(row["snapshot_id"])] = int(row["rating"])
    for item in extra or []:
        snapshot_id = item.get("snapshot_id")
        rating = item.get("rating")
        if snapshot_id is None or rating is None:
            continue
        found[str(snapshot_id)] = int(rating)
    return found


def count_match_runs() -> int:
    with open_db() as connection:
        return len(list_rows(connection, "match_run"))


def save_run(run: MatchRun) -> MatchRun:
    with open_db() as connection:
        insert(
            connection,
            "match_run",
            {
                "id": run.id,
                "profile_snapshot_id": run.profile_snapshot_id,
                "preference_id": run.preference_id,
                "method": run.method,
                "config": run.config,
                "config_hash": run.config_hash,
                "n_jobs": run.n_jobs,
            },
        )
        for result in run.results:
            result_id = result.id or new_id()
            result.id = result_id
            insert(
                connection,
                "match_result",
                {
                    "id": result_id,
                    "run_id": run.id,
                    "snapshot_id": result.snapshot_id,
                    "hard_result": result.hard_result,
                    "hard_reasons": result.hard_reasons,
                    "score": result.score,
                    "rank": result.rank,
                    "components": result.components,
                    "evidence": result.evidence,
                    "unknown_fields": result.unknown_fields,
                },
            )
    return run


def load_run(run_id: str) -> MatchRun | None:
    with open_db() as connection:
        row = get_by_id(connection, "match_run", run_id)
        if row is None:
            return None
        results = [item for item in list_rows(connection, "match_result") if item["run_id"] == run_id]
    return _run_from_row(row, results)


def latest_run() -> MatchRun | None:
    with open_db() as connection:
        rows = list_rows(connection, "match_run")
    if not rows:
        return None
    return load_run(str(rows[-1]["id"]))


def job_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    for snap in list_job_snapshots():
        titles[snap.id] = snap.title or snap.id
    return titles


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _fact_from_row(row: dict[str, Any]) -> Fact:
    structured = _loads(row.get("structured"))
    source_ref = _loads(row.get("source_ref"))
    return Fact(
        id=row["id"],
        profile_id=row["profile_id"],
        fact_key=row["fact_key"],
        revision=int(row["revision"]),
        category=row["category"],
        statement=row["statement"],
        structured=FactStructured.model_validate(structured) if isinstance(structured, dict) else None,
        source_ref=SourceRef.model_validate(source_ref) if isinstance(source_ref, dict) else None,
        status=row["status"],
        contribution=row["contribution"],
        is_estimate=bool(row["is_estimate"]),
        estimate_note=row["estimate_note"],
        confirmed_at=row["confirmed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _snapshot_from_row(row: dict[str, Any], platform: str) -> JobSnapshot:
    return JobSnapshot(
        id=row["id"],
        job_id=row["job_id"],
        platform=platform,  # type: ignore[arg-type]
        run_id=row["run_id"],
        fetched_at=row["fetched_at"],
        raw_path=row["raw_path"],
        content_hash=row["content_hash"],
        title=row["title"],
        company=row["company"],
        city=row["city"],
        district=row["district"],
        salary_text=row["salary_text"],
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_unit=row["salary_unit"],
        salary_months=row["salary_months"],
        experience_text=row["experience_text"],
        degree_text=row["degree_text"],
        description=row["description"],
        hr_title=row["hr_title"],
        hr_active_text=row["hr_active_text"],
        availability=row["availability"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _parse_from_row(row: dict[str, Any]) -> JDParse:
    stored = _loads(row.get("requirements"))
    requirements = None
    if isinstance(stored, list):
        requirements = [Requirement.model_validate(item) for item in stored]
    return JDParse(
        id=row["id"],
        snapshot_id=row["snapshot_id"],
        llm_call_id=row["llm_call_id"],
        status=row["status"],
        requirements=requirements,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_from_row(row: dict[str, Any], results: list[dict[str, Any]]) -> MatchRun:
    parsed_results: list[MatchResult] = []
    for item in sorted(results, key=lambda result: str(result["snapshot_id"])):
        parsed_results.append(
            MatchResult(
                id=item["id"],
                run_id=item["run_id"],
                snapshot_id=item["snapshot_id"],
                hard_result=item["hard_result"],
                hard_reasons=_string_list(item.get("hard_reasons")),
                score=item["score"],
                rank=item["rank"],
                components=_object(item.get("components")),
                evidence=_object(item.get("evidence")),
                unknown_fields=_string_list(item.get("unknown_fields")),
                created_at=item["created_at"],
                updated_at=item["updated_at"],
            )
        )
    config = _object(row.get("config")) or {}
    return MatchRun(
        id=row["id"],
        profile_snapshot_id=row["profile_snapshot_id"],
        preference_id=row["preference_id"],
        method=row["method"],
        config=config,
        config_hash=row["config_hash"],
        n_jobs=int(row["n_jobs"]),
        results=parsed_results,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _string_list(value: Any) -> list[str] | None:
    parsed = _loads(value)
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return None


def _object(value: Any) -> dict[str, Any] | None:
    parsed = _loads(value)
    if isinstance(parsed, dict):
        return parsed
    return None
