"""确定性校验，外加可选语义审查。

语义审查只能把 supported 降为 needs_review。
确定性失败保持 unsupported，不能被审查改成通过。
"""

from __future__ import annotations

from typing import Any

from jobbot.contracts import (
    Claim,
    ClaimCheck,
    ClaimReason,
    Fact,
    JobBotError,
    LLMResult,
    Material,
    ProfileSnapshot,
)
from jobbot.db.repository import get_by_id, update_by_id
from jobbot.materials.store import (
    connect_db,
    list_claims,
    load_material,
    row_to_claim,
    row_to_fact,
    row_to_material,
)
from jobbot.materials.text_rules import (
    contribution_ceiling,
    extract_dates,
    extract_numbers,
    extract_orgs,
    extract_skills,
    fact_dates,
    fact_numbers,
    fact_orgs,
    fact_skill_names,
    strongest_verb,
    timeline_reversed,
    CONTRIBUTION_RANK,
)

_REASON_ORDER = (
    "no_fact",
    "verb_escalation",
    "number_mismatch",
    "unknown_skill",
    "date_conflict",
    "org_mismatch",
    "semantic_unsupported",
)


def verify_claims(
    material: Material,
    profile: ProfileSnapshot,
    *,
    semantic_review: Any = None,
) -> list[ClaimCheck]:
    with connect_db() as connection:
        stored = get_by_id(connection, "material", material.id)
        if stored is None:
            raise JobBotError(
                "材料不存在。校验结果未保存。下一步：先生成草稿。",
                saved=False,
                next_step="生成草稿后再校验。",
            )
        current = row_to_material(stored)
        claims = [
            row_to_claim(row)
            for row in _claim_rows(connection, material.id)
        ]
        if current.status == "approved":
            return [_stored_check(claim) for claim in claims]
        facts_by_id = _facts_by_id(connection, profile.fact_ids)
        checks: list[ClaimCheck] = []
        for claim in claims:
            check = _evaluate(claim, profile, facts_by_id, semantic_review)
            update_by_id(
                connection,
                "claim",
                claim.id or "",
                {"check_status": check.check_status, "check_detail": check.detail},
            )
            checks.append(check)
        blocked = any(check.check_status != "supported" for check in checks)
        status = "verified" if checks and not blocked else "draft"
        if current.status != status:
            update_by_id(connection, "material", current.id, {"status": status})
        return checks


def claims_block_message(claims: list[Claim]) -> str | None:
    if not claims:
        return "没有已校验的陈述，不能批准或导出终稿。"
    for claim in claims:
        detail = claim.check_detail or {}
        if detail.get("pending_verify"):
            return "尚未完成校验，不能批准或导出终稿。"
        if claim.check_status in {"unsupported", "needs_review"}:
            return "仍有无依据或待复核的陈述，不能批准或导出终稿。"
    return None


def _claim_rows(connection, material_id: str) -> list[dict]:
    from jobbot.db.repository import list_rows

    rows = [row for row in list_rows(connection, "claim") if row["material_id"] == material_id]
    rows.sort(key=lambda row: (row["span_start"], row["id"]))
    return rows


def _facts_by_id(connection, fact_ids: list[str]) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}
    for fact_id in fact_ids:
        row = get_by_id(connection, "fact", fact_id)
        if row is None:
            continue
        facts[fact_id] = row_to_fact(row)
    return facts


def _evaluate(
    claim: Claim,
    profile: ProfileSnapshot,
    facts_by_id: dict[str, Fact],
    semantic_review: Any,
) -> ClaimCheck:
    reasons: list[ClaimReason] = []
    usable, missing = _usable_facts(claim, profile, facts_by_id)
    detail: dict[str, Any] = {"text": claim.text, "pending_verify": False}
    if claim.type == "factual" and missing:
        reasons.append("no_fact")
    if claim.type == "factual" and usable and "no_fact" not in reasons:
        reasons.extend(_factual_reasons(claim.text, usable))
        detail.update(_diagnostics(claim.text, usable))
    elif claim.type == "factual" and usable and "no_fact" in reasons:
        reasons.extend(_factual_reasons(claim.text, usable))
        detail.update(_diagnostics(claim.text, usable))
    status = "unsupported" if reasons else "supported"
    if semantic_review is not None:
        opinion = semantic_review(claim, usable)
        if status == "supported" and _downgrades(opinion):
            status = "needs_review"
            reasons = ["semantic_unsupported"]
    elif status == "supported" and "semantic_unsupported" in _prior_reasons(claim):
        status = "needs_review"
        reasons = ["semantic_unsupported"]
    detail["reasons"] = reasons
    return ClaimCheck(
        claim_id=claim.id or "",
        check_status=status,
        reasons=reasons,
        detail=detail,
    )


def _usable_facts(
    claim: Claim,
    profile: ProfileSnapshot,
    facts_by_id: dict[str, Fact],
) -> tuple[list[Fact], bool]:
    if claim.type != "factual":
        return [], False
    raw_ids = list(claim.fact_ids or [])
    if not raw_ids:
        return [], True
    allowed = set(profile.fact_ids)
    usable: list[Fact] = []
    missing = False
    for fact_id in raw_ids:
        fact = facts_by_id.get(fact_id)
        if fact_id not in allowed or fact is None or fact.status != "confirmed":
            missing = True
            continue
        usable.append(fact)
    if not usable:
        return [], True
    return usable, missing


def _factual_reasons(text: str, facts: list[Fact]) -> list[ClaimReason]:
    reasons: list[ClaimReason] = []
    verb = strongest_verb(text)
    ceiling = contribution_ceiling(facts)
    if verb is not None and (ceiling is None or CONTRIBUTION_RANK[verb] > ceiling):
        reasons.append("verb_escalation")
    allowed_numbers: set[str] = set()
    allowed_dates: set[str] = set()
    allowed_orgs: set[str] = set()
    allowed_skills: set[str] = set()
    for fact in facts:
        allowed_numbers |= fact_numbers(fact)
        allowed_dates |= fact_dates(fact)
        allowed_orgs |= fact_orgs(fact)
        allowed_skills |= fact_skill_names(fact)
    if not extract_numbers(text) <= allowed_numbers:
        reasons.append("number_mismatch")
    if not extract_skills(text) <= allowed_skills:
        reasons.append("unknown_skill")
    if not extract_dates(text) <= allowed_dates or timeline_reversed(text, facts):
        reasons.append("date_conflict")
    if not extract_orgs(text) <= allowed_orgs:
        reasons.append("org_mismatch")
    return reasons


def _diagnostics(text: str, facts: list[Fact]) -> dict[str, Any]:
    return {
        "verb": strongest_verb(text),
        "numbers": sorted(extract_numbers(text)),
        "dates": sorted(extract_dates(text)),
        "orgs": sorted(extract_orgs(text)),
        "skills": sorted(extract_skills(text)),
        "fact_ids": [fact.id for fact in facts],
    }


def _prior_reasons(claim: Claim) -> list[str]:
    detail = claim.check_detail or {}
    reasons = detail.get("reasons") or []
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons]
    return []


def _downgrades(opinion: object) -> bool:
    verdict = _verdict(opinion)
    return verdict in {"needs_review", "unsupported", "semantic_unsupported"}


def _verdict(opinion: object) -> str | None:
    if opinion is None:
        return None
    if isinstance(opinion, str):
        return opinion
    if isinstance(opinion, LLMResult):
        if opinion.status != "ok" or opinion.parsed is None:
            return None
        return getattr(opinion.parsed, "verdict", None)
    return getattr(opinion, "verdict", None)


def _stored_check(claim: Claim) -> ClaimCheck:
    detail = dict(claim.check_detail or {})
    raw_reasons = detail.get("reasons") or []
    reasons = [reason for reason in raw_reasons if reason in _REASON_ORDER]
    return ClaimCheck(
        claim_id=claim.id or "",
        check_status=claim.check_status,
        reasons=reasons,
        detail=detail or {"text": claim.text},
    )


def reload_claims(material_id: str) -> list[Claim]:
    material = load_material(material_id)
    del material
    return list_claims(material_id)
