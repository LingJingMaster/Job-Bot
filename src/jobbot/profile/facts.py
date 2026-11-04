"""事实的确认、拒绝、撤销和档案冻结。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from pydantic import ValidationError

from jobbot.contracts import (
    Claim,
    Fact,
    FactCandidate,
    FactEdits,
    FactStructured,
    JobBotError,
    ProfileSnapshot,
    SourceRef,
)
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id
from jobbot.profile._db import loads_json, open_db

logger = logging.getLogger("jobbot.profile")

_CONTRIBUTION_VERBS: dict[str, tuple[str, ...]] = {
    "lead": ("主导", "带领", "领导"),
    "push": ("推动", "推进"),
    "own": ("负责",),
    "participate": ("参与", "参加"),
    "support": ("协助", "配合", "支持"),
}


def ensure_profile(name: str = "默认档案") -> str:
    with open_db() as connection:
        rows = list_rows(connection, "profile")
        if rows:
            return str(rows[0]["id"])
        return insert(connection, "profile", {"name": name, "current_revision": 0})


def list_facts(profile_id: str) -> list[Fact]:
    with open_db() as connection:
        rows = [row for row in list_rows(connection, "fact") if row["profile_id"] == profile_id]
    return [_fact_from_row(row) for row in rows]


def list_snapshots(profile_id: str) -> list[ProfileSnapshot]:
    with open_db() as connection:
        rows = [row for row in list_rows(connection, "profile_snapshot") if row["profile_id"] == profile_id]
    return [_snapshot_from_row(row) for row in rows]


def list_claims(material_ids: list[str]) -> list[Claim]:
    wanted = set(material_ids)
    if not wanted:
        return []
    with open_db() as connection:
        rows = [row for row in list_rows(connection, "claim") if row["material_id"] in wanted]
    return [_claim_from_row(row) for row in rows]


def add_manual_fact(
    profile_id: str,
    *,
    category: str,
    statement: str,
    excerpt: str,
    contribution: str | None = None,
    is_estimate: bool = False,
    document_id: str | None = None,
    source_text: str | None = None,
) -> Fact:
    if not statement.strip() or not excerpt.strip():
        raise JobBotError(
            "手动录入缺少陈述或原文摘录。这条事实没有写入。",
            saved=False,
            next_step="填写陈述，并给出能在来源正文里逐字找到的摘录。",
        )
    with open_db() as connection:
        profile = get_by_id(connection, "profile", profile_id)
        if profile is None:
            raise JobBotError(
                "档案不存在。手动事实没有写入。",
                saved=False,
                next_step="先上传简历或保存偏好，以创建档案。",
            )
        if document_id:
            document = get_by_id(connection, "document", document_id)
            if document is None or not document["text"] or excerpt not in document["text"]:
                raise JobBotError(
                    "摘录在所选简历正文里逐字找不到。事实没有写入。",
                    saved=False,
                    next_step="改用正文中的原句作为摘录。",
                )
            source = document["text"]
        elif source_text is not None:
            if excerpt not in source_text:
                raise JobBotError(
                    "摘录不在你粘贴的来源正文里。事实没有写入。",
                    saved=False,
                    next_step="让摘录和来源正文逐字一致。",
                )
            source = source_text
        else:
            raise JobBotError(
                "手动事实没有来源正文。没有写入。",
                saved=False,
                next_step="选择已上传简历，或同时粘贴来源正文。",
            )
        checked = _require_contribution(contribution, excerpt)
        try:
            candidate = FactCandidate(
                category=category,  # type: ignore[arg-type]
                statement=statement.strip(),
                structured=FactStructured(org=None, role=None, start=None, end=None, skills=[], metrics=[]),
                contribution=checked,  # type: ignore[arg-type]
                is_estimate=is_estimate,
                source_ref=SourceRef(source_id=document_id, locator=_locator(source, excerpt), excerpt=excerpt),
            )
        except ValidationError as exc:
            raise JobBotError(
                "手动事实的字段不符合约定。没有写入。",
                saved=False,
                next_step="检查类别、陈述和贡献是否在允许的取值里。",
            ) from exc
        fact_id = _insert_candidate(connection, profile_id, candidate, revision=1)
        row = get_by_id(connection, "fact", fact_id)
    if row is None:
        raise JobBotError("手动事实写入后读不到。", saved=False, next_step="重试录入。")
    return _fact_from_row(row)


def confirm_fact(fact_id: str, edits: FactEdits | None = None) -> Fact:
    with open_db() as connection:
        row = get_by_id(connection, "fact", fact_id)
        if row is None:
            raise JobBotError(
                f"找不到事实 {fact_id}。没有改状态。",
                saved=False,
                next_step="刷新列表后重试。",
            )
        if row["status"] == "retracted":
            raise JobBotError(
                "这条事实已经撤销，不能直接确认。状态未改。",
                saved=False,
                next_step="按原文重新抽取或手动录入一条新的候选事实。",
            )
        if edits is None or _edits_empty(edits):
            if row["status"] != "confirmed":
                update_by_id(
                    connection,
                    "fact",
                    fact_id,
                    {"status": "confirmed", "confirmed_at": utc_now_iso()},
                )
            saved = get_by_id(connection, "fact", fact_id)
        else:
            saved_id = _insert_revision(connection, row, edits)
            saved = get_by_id(connection, "fact", saved_id)
    if saved is None:
        raise JobBotError("确认后的事实读不到。", saved=False, next_step="刷新页面查看列表。")
    return _fact_from_row(saved)


def retract_fact(fact_id: str, reason: str) -> list[str]:
    if not reason.strip():
        raise JobBotError(
            "撤销或拒绝需要填写原因。事实状态没有改。",
            saved=False,
            next_step="写明原因后再操作。",
        )
    with open_db() as connection:
        row = get_by_id(connection, "fact", fact_id)
        if row is None:
            raise JobBotError(
                f"找不到事实 {fact_id}。没有改状态。",
                saved=False,
                next_step="刷新列表后重试。",
            )
        if row["status"] == "retracted":
            raise JobBotError(
                "这条事实已经是撤销状态。没有再次修改。",
                saved=True,
                next_step="查看已撤销记录即可。",
            )
        was_confirmed = row["status"] == "confirmed"
        update_by_id(connection, "fact", fact_id, {"status": "retracted"})
        material_ids = _mark_materials(connection, fact_id, str(row["fact_key"]), reason.strip())
        if was_confirmed:
            _freeze(connection, str(row["profile_id"]))
        logger.info("fact_retracted id=%s materials=%s confirmed=%s", fact_id, len(material_ids), was_confirmed)
        return material_ids


def freeze_profile(profile_id: str) -> ProfileSnapshot:
    with open_db() as connection:
        profile = get_by_id(connection, "profile", profile_id)
        if profile is None:
            raise JobBotError(
                "档案不存在。没有生成快照。",
                saved=False,
                next_step="先创建档案并确认事实。",
            )
        snapshot_id = _freeze(connection, profile_id)
        row = get_by_id(connection, "profile_snapshot", snapshot_id)
    if row is None:
        raise JobBotError("档案快照写入后读不到。", saved=False, next_step="重试冻结。")
    return _snapshot_from_row(row)


def _freeze(connection, profile_id: str) -> str:
    profile = get_by_id(connection, "profile", profile_id)
    if profile is None:
        raise JobBotError("档案不存在。没有生成快照。", saved=False, next_step="先创建档案。")
    confirmed = _current_confirmed(connection, profile_id)
    fact_ids = [str(row["id"]) for row in confirmed]
    payload = [
        {
            "id": row["id"],
            "fact_key": row["fact_key"],
            "revision": row["revision"],
            "category": row["category"],
            "statement": row["statement"],
            "contribution": row["contribution"],
            "structured": loads_json(row["structured"]),
        }
        for row in confirmed
    ]
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    revision = int(profile["current_revision"] or 0) + 1
    snapshot_id = insert(
        connection,
        "profile_snapshot",
        {
            "profile_id": profile_id,
            "revision": revision,
            "fact_ids": fact_ids,
            "sha256": digest,
        },
    )
    update_by_id(connection, "profile", profile_id, {"current_revision": revision})
    return snapshot_id


def _current_confirmed(connection, profile_id: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in list_rows(connection, "fact"):
        if row["profile_id"] != profile_id:
            continue
        key = str(row["fact_key"])
        current = latest.get(key)
        if current is None or int(row["revision"]) >= int(current["revision"]):
            latest[key] = row
    chosen = [row for row in latest.values() if row["status"] == "confirmed"]
    chosen.sort(key=lambda row: str(row["fact_key"]))
    return chosen


def _mark_materials(connection, fact_id: str, fact_key: str, reason: str) -> list[str]:
    affected: list[str] = []
    claims_by_material: dict[str, list[dict[str, Any]]] = {}
    for claim in list_rows(connection, "claim"):
        claims_by_material.setdefault(str(claim["material_id"]), []).append(claim)
    for material in list_rows(connection, "material"):
        material_id = str(material["id"])
        hits = [
            claim
            for claim in claims_by_material.get(material_id, [])
            if fact_id in _id_list(claim.get("fact_ids")) or fact_key in _id_list(claim.get("fact_ids"))
        ]
        if not hits:
            continue
        for claim in hits:
            detail = loads_json(claim.get("check_detail"))
            if not isinstance(detail, dict):
                detail = {}
            detail["retracted_fact_id"] = fact_id
            detail["reason"] = reason
            update_by_id(
                connection,
                "claim",
                str(claim["id"]),
                {"check_status": "needs_review", "check_detail": detail},
            )
        update_by_id(connection, "material", material_id, {"status": "needs_input"})
        affected.append(material_id)
    return affected


def _insert_revision(connection, row: dict[str, Any], edits: FactEdits) -> str:
    source_ref = _source_ref(row)
    excerpt = source_ref.excerpt or ""
    contribution = row["contribution"]
    if edits.contribution is not None:
        contribution = _require_contribution(edits.contribution, excerpt)
    statement = edits.statement if edits.statement is not None else row["statement"]
    category = edits.category or row["category"]
    structured = edits.structured or _structured(row)
    is_estimate = row["is_estimate"] if edits.is_estimate is None else edits.is_estimate
    if isinstance(is_estimate, int):
        is_estimate = bool(is_estimate)
    revisions = [
        int(item["revision"])
        for item in list_rows(connection, "fact")
        if item["fact_key"] == row["fact_key"]
    ]
    candidate = FactCandidate(
        category=category,
        statement=statement,
        structured=structured,
        contribution=contribution,
        is_estimate=bool(is_estimate),
        source_ref=source_ref,
    )
    return _insert_candidate(
        connection,
        str(row["profile_id"]),
        candidate,
        revision=max(revisions, default=int(row["revision"])) + 1,
        fact_key=str(row["fact_key"]),
        status="confirmed",
        estimate_note=edits.estimate_note if edits.estimate_note is not None else row["estimate_note"],
        confirmed_at=utc_now_iso(),
    )


def _insert_candidate(
    connection,
    profile_id: str,
    candidate: FactCandidate,
    *,
    revision: int,
    fact_key: str | None = None,
    status: str = "candidate",
    estimate_note: str | None = None,
    confirmed_at: str | None = None,
) -> str:
    excerpt = candidate.source_ref.excerpt or ""
    key = fact_key or hashlib.sha256(f"{profile_id}\n{candidate.category}\n{excerpt}".encode()).hexdigest()
    return insert(
        connection,
        "fact",
        {
            "profile_id": profile_id,
            "fact_key": key,
            "revision": revision,
            "category": candidate.category,
            "statement": candidate.statement,
            "structured": candidate.structured.model_dump(mode="json"),
            "source_ref": candidate.source_ref.model_dump(mode="json"),
            "status": status,
            "contribution": candidate.contribution,
            "is_estimate": 1 if candidate.is_estimate else 0,
            "estimate_note": estimate_note,
            "confirmed_at": confirmed_at,
        },
    )


def _require_contribution(contribution: str | None, excerpt: str) -> str | None:
    if contribution is None:
        return None
    verbs = _CONTRIBUTION_VERBS.get(contribution, ())
    if any(verb in excerpt for verb in verbs):
        return contribution
    raise JobBotError(
        f"贡献强度「{contribution}」在摘录里没有对应动词。这条修改没有写入。",
        saved=False,
        next_step="改成摘录里出现的贡献，或把贡献留空。",
    )


def _edits_empty(edits: FactEdits) -> bool:
    return all(
        value is None
        for value in (
            edits.category,
            edits.statement,
            edits.structured,
            edits.contribution,
            edits.is_estimate,
            edits.estimate_note,
        )
    )


def _locator(text: str, excerpt: str) -> str:
    index = text.find(excerpt)
    if index < 0:
        return "p1:para1"
    prefix = text[:index]
    page = prefix.count("\f") + 1
    paragraph = prefix.split("\f")[-1].count("\n") + 1
    return f"p{page}:para{paragraph}"


def _id_list(value: Any) -> list[str]:
    parsed = loads_json(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _structured(row: dict[str, Any]) -> FactStructured:
    parsed = loads_json(row.get("structured"))
    if isinstance(parsed, dict):
        return FactStructured.model_validate(parsed)
    return FactStructured(org=None, role=None, start=None, end=None, skills=[], metrics=[])


def _source_ref(row: dict[str, Any]) -> SourceRef:
    parsed = loads_json(row.get("source_ref"))
    if isinstance(parsed, dict):
        return SourceRef.model_validate(parsed)
    return SourceRef()


def _fact_from_row(row: dict[str, Any]) -> Fact:
    structured = loads_json(row.get("structured"))
    source_ref = loads_json(row.get("source_ref"))
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


def _snapshot_from_row(row: dict[str, Any]) -> ProfileSnapshot:
    fact_ids = loads_json(row["fact_ids"])
    return ProfileSnapshot(
        id=row["id"],
        profile_id=row["profile_id"],
        revision=int(row["revision"]),
        fact_ids=[str(item) for item in fact_ids] if isinstance(fact_ids, list) else [],
        sha256=row["sha256"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _claim_from_row(row: dict[str, Any]) -> Claim:
    return Claim(
        id=row["id"],
        material_id=row["material_id"],
        span_start=int(row["span_start"]),
        span_end=int(row["span_end"]),
        text=row["text"],
        type=row["type"],
        fact_ids=_id_list(row.get("fact_ids")),
        requirement_ids=_id_list(row.get("requirement_ids")),
        check_status=row["check_status"],
        check_detail=loads_json(row.get("check_detail")) if isinstance(loads_json(row.get("check_detail")), dict) else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
