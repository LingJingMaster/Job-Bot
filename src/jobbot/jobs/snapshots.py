"""岗位快照。有 external_id 时复用 Job；相同内容不新增快照。"""

from __future__ import annotations

import hashlib
from typing import Any

from jobbot.contracts import Job, JobBotError, JobSnapshot, JobSnapshotDraft, ParseError
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id
from jobbot.jobs._db import open_db
from jobbot.jobs.salary import parse_salary

def content_fingerprint(draft: JobSnapshotDraft) -> str:
    """与采集器相同：只哈希 JD 全文。传入的 content_hash 不采用，避免两套入库算出不同快照。"""
    description = draft.description or ""
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def upsert_snapshot(draft: JobSnapshotDraft) -> tuple[Job, JobSnapshot, bool]:
    if draft.description is None:
        raise ParseError(
            "岗位缺少描述，没有写入快照。",
            saved=False,
            next_step="补上岗位描述后再入库。",
        )
    filled = _with_salary(draft)
    fingerprint = content_fingerprint(filled)
    external_id = filled.external_id.strip() if filled.external_id else None
    if external_id == "":
        external_id = None
    with open_db() as connection:
        job_row = _find_job(connection, filled.platform, external_id)
        if job_row is None:
            job_id = insert(
                connection,
                "job",
                {
                    "platform": filled.platform,
                    "external_id": external_id,
                    "url": filled.url,
                    "dedup_group": None,
                    "latest_snapshot_id": None,
                },
            )
        else:
            job_id = str(job_row["id"])
        existing = _find_snapshot(connection, job_id, fingerprint)
        if existing is not None:
            job = _job_from_row(get_by_id(connection, "job", job_id))
            return job, _snapshot_from_row(existing, filled.platform), False
        snapshot_id = insert(
            connection,
            "job_snapshot",
            {
                "job_id": job_id,
                "run_id": None,
                "fetched_at": utc_now_iso(),
                "raw_path": filled.raw_path,
                "content_hash": fingerprint,
                "title": filled.title,
                "company": filled.company,
                "city": filled.city,
                "district": filled.district,
                "salary_text": filled.salary_text,
                "salary_min": filled.salary_min,
                "salary_max": filled.salary_max,
                "salary_unit": filled.salary_unit,
                "salary_months": filled.salary_months,
                "experience_text": filled.experience_text,
                "degree_text": filled.degree_text,
                "description": filled.description,
                "hr_title": filled.hr_title,
                "hr_active_text": filled.hr_active_text,
                "availability": filled.availability,
                "source": filled.source,
            },
        )
        fields: dict[str, Any] = {"latest_snapshot_id": snapshot_id}
        if filled.url:
            fields["url"] = filled.url
        update_by_id(connection, "job", job_id, fields)
        job = _job_from_row(get_by_id(connection, "job", job_id))
        snapshot = _snapshot_from_row(get_by_id(connection, "job_snapshot", snapshot_id), filled.platform)
    return job, snapshot, True


def _with_salary(draft: JobSnapshotDraft) -> JobSnapshotDraft:
    structured_missing = (
        draft.salary_min is None
        and draft.salary_max is None
        and draft.salary_months is None
        and draft.salary_unit is None
    )
    if not draft.salary_text or not structured_missing:
        return draft
    parsed = parse_salary(draft.salary_text)
    return draft.model_copy(
        update={
            "salary_text": parsed.salary_text,
            "salary_min": parsed.min,
            "salary_max": parsed.max,
            "salary_unit": parsed.salary_unit,
            "salary_months": parsed.months,
        }
    )


def _find_job(connection, platform: str, external_id: str | None) -> dict[str, Any] | None:
    if not external_id:
        return None
    for row in list_rows(connection, "job"):
        if row["platform"] == platform and row["external_id"] == external_id:
            return row
    return None


def _find_snapshot(connection, job_id: str, fingerprint: str) -> dict[str, Any] | None:
    for row in list_rows(connection, "job_snapshot"):
        if row["job_id"] == job_id and row["content_hash"] == fingerprint:
            return row
    return None


def _job_from_row(row: dict[str, Any] | None) -> Job:
    if row is None:
        raise JobBotError("岗位写入后读不到。", saved=False, next_step="重试入库。")
    return Job(
        id=row["id"],
        platform=row["platform"],
        external_id=row["external_id"],
        url=row["url"],
        dedup_group=row["dedup_group"],
        latest_snapshot_id=row["latest_snapshot_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _snapshot_from_row(row: dict[str, Any] | None, platform: str) -> JobSnapshot:
    if row is None:
        raise JobBotError("岗位快照写入后读不到。", saved=False, next_step="重试入库。")
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

