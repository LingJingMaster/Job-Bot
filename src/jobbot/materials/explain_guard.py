"""解释引用无效时退回模板，不展示无效引用。

匹配模块的 explain 仍由 WP-D 实现。这里只提供可单独调用的守卫，避免改 matching/。
"""

from __future__ import annotations

import re

from pydantic import ConfigDict

from jobbot.contracts import EXPLANATION_SCORE_NOTE, ContractModel, Explanation

_HEX_ID = re.compile(r"\b[0-9a-fA-F]{32}\b")


class ExplanationCitation(ContractModel):
    """一条待展示的引用。excerpt 必须能在对应原文里逐字找到。"""

    model_config = ConfigDict(extra="forbid")

    fact_id: str | None = None
    excerpt: str | None = None
    source_id: str | None = None
    span: list[int] | None = None
    text: str = ""


def guard_explanation(
    matched: list[str],
    gaps: list[str],
    unknowns: list[str],
    citations: list[ExplanationCitation],
    *,
    matched_requirement_count: int,
    known_fact_ids: set[str],
    sources: dict[str, str],
) -> Explanation:
    """引用校验失败时只返回模板句，原文摘录和无效 fact_id 都不会出现。"""
    texts = [*matched, *gaps, *unknowns]
    if _citations_invalid(citations, known_fact_ids, sources) or _texts_cite_unknown_fact(texts, known_fact_ids):
        return _template(matched_requirement_count)
    for citation in citations:
        if citation.excerpt and citation.excerpt not in "\n".join(sources.values()) and citation.source_id is None:
            return _template(matched_requirement_count)
    return Explanation(
        matched=list(matched),
        gaps=list(gaps),
        unknowns=list(unknowns),
        used_template=False,
        score_note=EXPLANATION_SCORE_NOTE,
    )


def _template(matched_requirement_count: int) -> Explanation:
    try:
        count = int(matched_requirement_count)
    except (TypeError, ValueError):
        count = 0
    if count < 0:
        count = 0
    line = f"与该岗位 {count} 项要求匹配度较高。"
    return Explanation(
        matched=[line],
        gaps=[],
        unknowns=[],
        used_template=True,
        score_note=EXPLANATION_SCORE_NOTE,
    )


def _citations_invalid(
    citations: list[ExplanationCitation],
    known_fact_ids: set[str],
    sources: dict[str, str],
) -> bool:
    for citation in citations:
        fact_id = (citation.fact_id or "").strip()
        if fact_id and fact_id not in known_fact_ids:
            return True
        excerpt = citation.excerpt
        if excerpt:
            if citation.source_id:
                source = sources.get(citation.source_id)
                if source is None or excerpt not in source:
                    return True
                if citation.span is not None and not _span_matches(source, citation.span, excerpt):
                    return True
            elif not any(excerpt in source for source in sources.values()):
                return True
        elif citation.span is not None:
            return True
    return False


def _span_matches(source: str, span: list[int], excerpt: str) -> bool:
    if len(span) != 2:
        return False
    start, end = span
    if start < 0 or end > len(source) or start > end:
        return False
    return source[start:end] == excerpt


def _texts_cite_unknown_fact(texts: list[str], known_fact_ids: set[str]) -> bool:
    known = {fact_id.lower() for fact_id in known_fact_ids}
    for text in texts:
        for found in _HEX_ID.findall(text):
            if found.lower() not in known:
                return True
    return False
