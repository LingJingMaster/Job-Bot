"""由已确认事实拼出材料。模型草拟另走 llm_generate，并且只接受 LLMResult。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from jobbot.contracts import EvidenceLink, Fact, JobBotError, JobSnapshot, Material, ProfileSnapshot
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id
from jobbot.materials.store import (
    connect_db,
    list_claims,
    load_confirmed_or_raise,
    load_job_snapshot,
    load_material,
    load_profile_snapshot,
    row_to_material,
)

_KINDS = {"resume", "ats_text", "opening_msg"}
_PLAIN_MODES = {"pipeline", "polish", "raw"}
_HEADINGS = {
    "education": "教育",
    "work": "工作",
    "project": "项目",
    "skill": "技能",
    "award": "奖项",
    "certificate": "证书",
    "other": "其他",
}
_CATEGORY_ORDER = ("work", "project", "education", "skill", "award", "certificate", "other")


@dataclass(frozen=True)
class DraftLine:
    text: str
    claim_type: str
    fact_ids: list[str]
    requirement_ids: list[str]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def draft_material(
    kind: str,
    snap: JobSnapshot,
    profile: ProfileSnapshot,
    links: list[EvidenceLink],
    mode: str,
) -> Material:
    _require_kind_and_mode(kind, mode)
    with connect_db() as connection:
        facts = load_confirmed_or_raise(connection, profile)
        lines = build_lines(snap, facts, links)
        return _persist(connection, kind, snap, profile, mode, lines, parent_id=None)


def revise_material(material_id: str, content: str) -> Material:
    current = load_material(material_id)
    if current.content == content:
        return current
    if current.status == "approved":
        parent_id = current.id
        supersede = False
    else:
        parent_id = current.id
        supersede = True
    claims = list_claims(material_id)
    factual = [claim for claim in claims if claim.type == "factual"]
    lines: list[DraftLine] = []
    factual_index = 0
    for line in content.split("\n"):
        if line.startswith("- "):
            previous = factual[factual_index] if factual_index < len(factual) else None
            factual_index += 1
            lines.append(
                DraftLine(
                    text=line,
                    claim_type="factual",
                    fact_ids=list(previous.fact_ids or []) if previous is not None else [],
                    requirement_ids=list(previous.requirement_ids or []) if previous is not None else [],
                )
            )
        elif line.strip():
            lines.append(DraftLine(text=line, claim_type="non_factual", fact_ids=[], requirement_ids=[]))
    if not lines:
        raise JobBotError(
            "正文是空的。没有保存修改。下一步：写上要保存的正文。",
            saved=False,
            next_step="保留至少一行正文。",
        )
    profile = load_profile_snapshot(current.profile_snapshot_id)
    snap = load_job_snapshot(current.snapshot_id)
    with connect_db() as connection:
        load_confirmed_or_raise(connection, profile)
        material = _persist(
            connection,
            current.kind,
            snap,
            profile,
            current.mode,
            lines,
            parent_id=parent_id,
        )
        if supersede:
            update_by_id(connection, "material", current.id, {"status": "superseded"})
        return material


def material_from_llm_parsed(kind: str, snap: JobSnapshot, profile: ProfileSnapshot, mode: str, parsed) -> Material:
    """把模型输出落成材料。factual 且没有 fact_ids 的条目记为 unsupported。"""
    _require_kind_and_mode(kind, mode)
    lines: list[DraftLine] = []
    for section in getattr(parsed, "sections", []) or []:
        heading = getattr(section, "heading", None)
        if heading:
            lines.append(DraftLine(text=str(heading), claim_type="non_factual", fact_ids=[], requirement_ids=[]))
        for bullet in getattr(section, "bullets", []) or []:
            text = str(bullet.text).strip()
            claim_type = bullet.type
            fact_ids = list(bullet.fact_ids or [])
            if claim_type == "factual" and not text.startswith("- "):
                text = f"- {text}"
            lines.append(
                DraftLine(
                    text=text,
                    claim_type=claim_type,
                    fact_ids=fact_ids,
                    requirement_ids=list(bullet.requirement_ids or []),
                )
            )
    if not lines:
        lines.append(DraftLine(text="（模型没有返回正文）", claim_type="non_factual", fact_ids=[], requirement_ids=[]))
    with connect_db() as connection:
        load_confirmed_or_raise(connection, profile)
        return _persist(connection, kind, snap, profile, mode, lines, parent_id=None)


def build_lines(snap: JobSnapshot, facts: list[Fact], links: list[EvidenceLink]) -> list[DraftLine]:
    lines: list[DraftLine] = []
    if snap.title:
        lines.append(DraftLine(text=f"目标岗位：{snap.title}", claim_type="non_factual", fact_ids=[], requirement_ids=[]))
    if snap.company:
        lines.append(DraftLine(text=f"目标公司：{snap.company}", claim_type="non_factual", fact_ids=[], requirement_ids=[]))
    by_id = {fact.id: fact for fact in facts}
    requirement_ids: dict[str, list[str]] = {}
    ordered: list[str] = []
    for link in links:
        if link.status not in {"covered", "weak"}:
            continue
        for fact_id in link.fact_ids:
            if fact_id not in by_id:
                continue
            bucket = requirement_ids.setdefault(fact_id, [])
            if link.requirement_id not in bucket:
                bucket.append(link.requirement_id)
            if fact_id not in ordered:
                ordered.append(fact_id)
    grouped: dict[str, list[Fact]] = {}
    for fact_id in ordered:
        fact = by_id[fact_id]
        grouped.setdefault(fact.category, []).append(fact)
    for category in _CATEGORY_ORDER:
        group = grouped.get(category)
        if not group:
            continue
        lines.append(DraftLine(text=_HEADINGS[category], claim_type="non_factual", fact_ids=[], requirement_ids=[]))
        for fact in group:
            lines.append(
                DraftLine(
                    text=f"- {fact.statement}",
                    claim_type="factual",
                    fact_ids=[fact.id],
                    requirement_ids=requirement_ids.get(fact.id, []),
                )
            )
    if not any(line.claim_type == "factual" for line in lines):
        lines.append(DraftLine(text="没有可写入的已确认事实。", claim_type="non_factual", fact_ids=[], requirement_ids=[]))
    return lines


def _persist(connection, kind: str, snap: JobSnapshot, profile: ProfileSnapshot, mode: str, lines: list[DraftLine], parent_id: str | None) -> Material:
    content = "\n".join(line.text for line in lines)
    digest = content_hash(content)
    existing = [
        row
        for row in list_rows(connection, "material")
        if row["profile_snapshot_id"] == profile.id and row["snapshot_id"] == snap.id and row["kind"] == kind
    ]
    revision = max((int(row["revision"]) for row in existing), default=0) + 1
    payload = {
        "profile_snapshot_id": profile.id,
        "snapshot_id": snap.id,
        "kind": kind,
        "mode": mode,
        "revision": revision,
        "content": content,
        "status": "draft",
        "content_hash": digest,
    }
    if parent_id:
        payload["parent_id"] = parent_id
    material_id = insert(connection, "material", payload)
    cursor = 0
    for line in lines:
        start = content.find(line.text, cursor)
        if start < 0:
            raise JobBotError(
                "正文定位失败。这份材料的陈述未写完。下一步：重新生成草稿。",
                saved=True,
                next_step="重新生成草稿。",
            )
        end = start + len(line.text)
        cursor = end
        missing_facts = line.claim_type == "factual" and not line.fact_ids
        insert(
            connection,
            "claim",
            {
                "material_id": material_id,
                "span_start": start,
                "span_end": end,
                "text": line.text,
                "type": line.claim_type,
                "fact_ids": line.fact_ids,
                "requirement_ids": line.requirement_ids,
                "check_status": "unsupported" if missing_facts else "needs_review",
                "check_detail": {
                    "text": line.text,
                    "reasons": ["no_fact"] if missing_facts else [],
                    "pending_verify": not missing_facts,
                },
            },
        )
    for row in existing:
        if row["status"] == "approved":
            continue
        update_by_id(connection, "material", str(row["id"]), {"status": "superseded"})
    stored = get_by_id(connection, "material", material_id)
    assert stored is not None
    return row_to_material(stored)


def _require_kind_and_mode(kind: str, mode: str) -> None:
    if kind not in _KINDS:
        raise JobBotError(
            "材料类型必须是 resume、ats_text 或 opening_msg。材料未保存。",
            saved=False,
            next_step="改用这三种类型之一。",
        )
    if mode not in _PLAIN_MODES and not mode.startswith("ablation_"):
        raise JobBotError(
            "mode 必须是 pipeline、polish、raw 或 ablation_*。材料未保存。",
            saved=False,
            next_step="改用约定的 mode。",
        )
