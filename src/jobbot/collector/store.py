"""岗位与快照入库。有 external_id 时复用 Job；同一 content_hash 不新增快照。"""

from __future__ import annotations

import hashlib
import sqlite3

from jobbot.contracts import Job, JobSnapshot, JobSnapshotDraft, ParseError
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id

def content_hash_for(draft: JobSnapshotDraft) -> str:
    """同一岗位只在 JD 全文变化时新增快照。标题或薪资原文变化不单独开快照。"""
    description = draft.description or ""
    return hashlib.sha256(description.encode("utf-8")).hexdigest()




def save_draft(
    connection: sqlite3.Connection,
    draft: JobSnapshotDraft,
    *,
    run_id: str | None = None,
) -> tuple[Job, JobSnapshot, bool]:
    if not draft.description:
        raise ParseError(
            "职位描述为空，不能入库。",
            saved=False,
            next_step="补上职位描述后再保存。",
        )
    content_hash = draft.content_hash or content_hash_for(draft)
    job_row = _find_job(connection, draft.platform, draft.external_id)
    if job_row is None and not draft.external_id:
        existing = _find_snapshot_by_hash(connection, draft.platform, content_hash, external_id_missing=True)
        if existing is not None:
            job_row = get_by_id(connection, "job", existing["job_id"])
            return _job_from_row(job_row), _snapshot_from_row(existing, job_row), False
    if job_row is None:
        job_id = insert(
            connection,
            "job",
            {
                "platform": draft.platform,
                "external_id": draft.external_id,
                "url": draft.url,
                "dedup_group": None,
                "latest_snapshot_id": None,
            },
        )
        job_row = get_by_id(connection, "job", job_id)
    else:
        job_id = str(job_row["id"])
        matched = _snapshot_for_job(connection, job_id, content_hash)
        if matched is not None:
            return _job_from_row(job_row), _snapshot_from_row(matched, job_row), False

    snapshot_id = insert(
        connection,
        "job_snapshot",
        {
            "job_id": job_id,
            "run_id": run_id,
            "fetched_at": utc_now_iso(),
            "raw_path": draft.raw_path,
            "content_hash": content_hash,
            "title": draft.title,
            "company": draft.company,
            "city": draft.city,
            "district": draft.district,
            "salary_text": draft.salary_text,
            "salary_min": draft.salary_min,
            "salary_max": draft.salary_max,
            "salary_unit": draft.salary_unit,
            "salary_months": draft.salary_months,
            "experience_text": draft.experience_text,
            "degree_text": draft.degree_text,
            "description": draft.description,
            "hr_title": draft.hr_title,
            "hr_active_text": draft.hr_active_text,
            "availability": draft.availability,
            "source": draft.source,
        },
    )
    job_fields: dict[str, str | None] = {"latest_snapshot_id": snapshot_id}
    if draft.url:
        job_fields["url"] = draft.url
    update_by_id(connection, "job", job_id, job_fields)
    job_row = get_by_id(connection, "job", job_id)
    snapshot_row = get_by_id(connection, "job_snapshot", snapshot_id)
    return _job_from_row(job_row), _snapshot_from_row(snapshot_row, job_row), True


def snapshot_rows(connection: sqlite3.Connection) -> list[dict]:
    return list_rows(connection, "job_snapshot")


def _find_job(connection: sqlite3.Connection, platform: str, external_id: str | None) -> dict | None:
    if not external_id:
        return None
    for row in list_rows(connection, "job"):
        if row["platform"] == platform and row["external_id"] == external_id:
            return row
    return None


def _snapshot_for_job(connection: sqlite3.Connection, job_id: str, content_hash: str) -> dict | None:
    for row in list_rows(connection, "job_snapshot"):
        if row["job_id"] == job_id and row["content_hash"] == content_hash:
            return row
    return None


def _find_snapshot_by_hash(
    connection: sqlite3.Connection,
    platform: str,
    content_hash: str,
    *,
    external_id_missing: bool,
) -> dict | None:
    jobs = {
        row["id"]: row
        for row in list_rows(connection, "job")
        if row["platform"] == platform and (row["external_id"] is None if external_id_missing else True)
    }
    for row in list_rows(connection, "job_snapshot"):
        if row["content_hash"] == content_hash and row["job_id"] in jobs:
            return row
    return None


def _job_from_row(row: dict | None) -> Job:
    if row is None:
        raise ParseError("岗位行不存在。数据未保存。", saved=False, next_step="重新导入这一条。")
    return Job.model_validate(dict(row))


def _snapshot_from_row(row: dict, job_row: dict | None) -> JobSnapshot:
    payload = dict(row)
    if job_row is not None:
        payload["platform"] = job_row["platform"]
        payload["external_id"] = job_row["external_id"]
        if payload.get("url") in (None, "") and job_row.get("url"):
            payload["url"] = job_row["url"]
    return JobSnapshot.model_validate(payload)
