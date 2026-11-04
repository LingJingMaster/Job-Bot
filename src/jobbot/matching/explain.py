"""解释只读已保存的特征和证据，不重新打分。"""

from __future__ import annotations

from typing import Any

from jobbot.contracts import EXPLANATION_SCORE_NOTE, Explanation, MatchResult
from jobbot.materials.explain_guard import ExplanationCitation, guard_explanation


def explain(result: MatchResult) -> Explanation:
    evidence = result.evidence if isinstance(result.evidence, dict) else {}
    known = {str(item) for item in evidence.get("known_fact_ids") or []}
    sources = evidence.get("sources") if isinstance(evidence.get("sources"), dict) else {}
    source_map = {str(key): str(value) for key, value in sources.items()}
    matched_rows = _rows(evidence.get("matched"))
    gap_rows = _rows(evidence.get("gaps"))
    unknown_rows = _rows(evidence.get("unknowns"))
    citations = [_citation(row) for row in matched_rows if row.get("fact_id") or row.get("excerpt")]
    count = evidence.get("matched_requirement_count")
    try:
        matched_count = int(count) if count is not None else 0
    except (TypeError, ValueError):
        matched_count = 0
    explanation = guard_explanation(
        [row.get("text", "") for row in matched_rows],
        [row.get("text", "") for row in gap_rows],
        [row.get("text", "") for row in unknown_rows],
        citations,
        matched_requirement_count=matched_count,
        known_fact_ids=known,
        sources=source_map,
    )
    if explanation.score_note != EXPLANATION_SCORE_NOTE:
        return explanation.model_copy(update={"score_note": EXPLANATION_SCORE_NOTE})
    return explanation


def _rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append({str(key): "" if item[key] is None else str(item[key]) for key in item})
        elif isinstance(item, str):
            rows.append({"text": item})
    return rows


def _citation(row: dict[str, str]) -> ExplanationCitation:
    fact_id = row.get("fact_id") or None
    excerpt = row.get("excerpt") or None
    source_id = row.get("source_id") or fact_id
    return ExplanationCitation(
        fact_id=fact_id,
        excerpt=excerpt,
        source_id=source_id,
        text=row.get("text", ""),
    )
