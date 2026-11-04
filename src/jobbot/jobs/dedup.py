"""无 external_id 时只提示可能重复的岗位，不合并。"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from jobbot.contracts import JobSnapshotDraft
from jobbot.db.repository import list_rows
from jobbot.jobs._db import open_db
from jobbot.jobs.aliases import canonical_city

_SIMILARITY_THRESHOLD = 0.6


class DuplicateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    snapshot_id: str
    reason: str


def dedup(draft: JobSnapshotDraft) -> list[DuplicateSuggestion]:
    with open_db() as connection:
        jobs = {str(row["id"]): row for row in list_rows(connection, "job")}
        snapshots = list_rows(connection, "job_snapshot")
    suggestions: list[DuplicateSuggestion] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        job = jobs.get(str(snapshot["job_id"]))
        if job is None or str(job["id"]) in seen:
            continue
        if _same_external_id(draft, job):
            continue
        reason = _link_reason(draft, job, snapshot)
        if reason is None:
            continue
        seen.add(str(job["id"]))
        suggestions.append(
            DuplicateSuggestion(job_id=str(job["id"]), snapshot_id=str(snapshot["id"]), reason=reason)
        )
    return suggestions


def _same_external_id(draft: JobSnapshotDraft, job: dict) -> bool:
    external_id = draft.external_id.strip() if draft.external_id else ""
    if not external_id or not job.get("external_id"):
        return False
    return job["platform"] == draft.platform and job["external_id"] == external_id


def _link_reason(draft: JobSnapshotDraft, job: dict, snapshot: dict) -> str | None:
    draft_url = _canonical_url(draft.url)
    job_url = _canonical_url(job.get("url"))
    if draft_url and job_url and draft_url == job_url:
        return "规范 URL 相同"
    if _same_posting_key(draft, snapshot) and _similar(draft.description or "", str(snapshot["description"])) >= _SIMILARITY_THRESHOLD:
        return "公司、岗位、地点相同且正文相似"
    return None


def _same_posting_key(draft: JobSnapshotDraft, snapshot: dict) -> bool:
    company = _compact(draft.company)
    title = _compact(draft.title)
    city = canonical_city(draft.city)
    if not company or not title or not city:
        return False
    return (
        company == _compact(snapshot.get("company"))
        and title == _compact(snapshot.get("title"))
        and city == canonical_city(snapshot.get("city"))
    )


def _compact(value: object) -> str:
    if value is None:
        return ""
    return "".join(str(value).split()).casefold()


def _canonical_url(url: object) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip().rstrip("/").casefold()
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def _similar(left: str, right: str) -> float:
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    shared = left_grams & right_grams
    return len(shared) / len(left_grams | right_grams)


def _bigrams(text: str) -> set[str]:
    compact = "".join(text.split())
    if len(compact) < 2:
        return set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}
