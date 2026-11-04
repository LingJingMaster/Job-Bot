"""§3.3 特征。缺失保持 null，不用 0 表示不满足。"""

from __future__ import annotations

import re

from jobbot.contracts import Fact, Features, JDParse, JobSnapshot, Preference, ProfileSnapshot, Requirement
from jobbot.jobs.aliases import equivalent_terms, skills_equivalent
from jobbot.matching.embed import embedding_status, max_mapped_cosine
from jobbot.matching.textutil import contains_term, is_demographic, ranking_text
from jobbot.matching.tfidf import FittedTfidf, cosine

_DEGREE_RANKS: tuple[tuple[str, int], ...] = (
    ("博士", 6),
    ("硕士", 5),
    ("本科", 4),
    ("大专", 3),
    ("专科", 3),
    ("高中", 2),
    ("中专", 2),
)
_YEAR = re.compile(r"(\d{4})")
_EXPERIENCE = re.compile(r"(\d+)\s*年")

_fitted: FittedTfidf | None = None
_profile_name: str | None = None


def bind_tfidf(fitted: FittedTfidf | None, profile_name: str | None = None) -> None:
    global _fitted, _profile_name
    _fitted = fitted
    _profile_name = profile_name


def compute_features(
    profile: ProfileSnapshot,
    snap: JobSnapshot,
    parse: JDParse,
    pref: Preference,
) -> Features:
    from jobbot.matching.store import load_snapshot_facts, profile_name

    profile_facts = load_snapshot_facts(profile)
    usable = [fact for fact in profile_facts if not is_demographic(fact)]
    name = _profile_name if _profile_name is not None else profile_name(profile.profile_id)
    profile_body = ranking_text(profile_facts, name)
    job_body = snap.description or ""
    requirements = list(parse.requirements or []) if parse.status == "ok" else []
    return Features(
        f_sem=_semantic(profile_body, job_body),
        f_skill=_skill(requirements, usable),
        f_resp=_responsibility(requirements, usable),
        f_pref=_preference(snap, pref),
        f_tfidf=_tfidf(profile_body, job_body),
        f_exp=_experience(snap, usable),
    )


def skill_evidence(
    requirements: list[Requirement],
    facts: list[Fact],
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    matched: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = []
    hits = 0
    for requirement in requirements:
        if requirement.type != "core_skill":
            continue
        fact = _supporting_fact(requirement, facts)
        if fact is None or fact.id is None:
            gaps.append({"text": f"技能要求没有确认事实：{requirement.text}"})
            continue
        hits += 1
        excerpt = (fact.statement or "").strip()
        matched.append(
            {
                "text": f"技能要求有确认事实：{requirement.text}",
                "fact_id": fact.id,
                "excerpt": excerpt,
                "source_id": fact.id,
            }
        )
    return matched, gaps, hits


def _semantic(profile_body: str, job_body: str) -> float | None:
    ready, _message = embedding_status()
    if not ready:
        return None
    return max_mapped_cosine(profile_body, job_body)


def _tfidf(profile_body: str, job_body: str) -> float | None:
    if _fitted is None or not profile_body.strip() or not job_body.strip():
        return None
    return cosine(_fitted.transform(profile_body), _fitted.transform(job_body))


def _skill(requirements: list[Requirement], facts: list[Fact]) -> float | None:
    targeted = [item for item in requirements if item.type == "core_skill"]
    if not targeted:
        return None
    weight_sum = 0
    hit_sum = 0.0
    for requirement in targeted:
        weight_sum += requirement.weight
        if _supporting_fact(requirement, facts) is not None:
            hit_sum += requirement.weight
    if weight_sum <= 0:
        return None
    return hit_sum / weight_sum


def _responsibility(requirements: list[Requirement], facts: list[Fact]) -> float | None:
    targeted = [item for item in requirements if item.type == "responsibility"]
    if not targeted:
        return None
    ready, _message = embedding_status()
    if not ready:
        return None
    statements = [fact.statement.strip() for fact in facts if fact.statement and fact.statement.strip()]
    if not statements:
        return None
    weight_sum = 0
    total = 0.0
    for requirement in targeted:
        scores = [max_mapped_cosine(statement, requirement.text) for statement in statements]
        usable = [score for score in scores if score is not None]
        if not usable:
            continue
        weight_sum += requirement.weight
        total += requirement.weight * max(usable)
    if weight_sum <= 0:
        return None
    return total / weight_sum


def _preference(snap: JobSnapshot, pref: Preference) -> float | None:
    if not pref.soft:
        return None
    scores: list[float] = []
    for item in pref.soft:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("kind") or "")
        if field == "city":
            score = _city_pref(snap, item.get("value", item.get("values")))
        elif field in {"keyword", "keywords"}:
            score = _keyword_pref(snap, item.get("value", item.get("values")))
        elif field == "salary":
            score = _salary_pref(snap, item.get("min"), item.get("max"))
        else:
            continue
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    return sum(scores) / len(scores)


def _city_pref(snap: JobSnapshot, value: object) -> float | None:
    from jobbot.jobs.aliases import canonical_city

    actual = canonical_city(snap.city)
    if actual is None:
        return None
    raw_values = value if isinstance(value, list) else [value]
    expected = [canonical_city(str(item)) for item in raw_values]
    expected = [item for item in expected if item]
    if not expected:
        return None
    return 1.0 if actual in expected else 0.0


def _keyword_pref(snap: JobSnapshot, value: object) -> float | None:
    raw_values = value if isinstance(value, list) else [value]
    terms = [str(item).strip() for item in raw_values if str(item).strip()]
    haystack = "\n".join(part for part in (snap.title, snap.description) if part)
    if not terms or not haystack.strip():
        return None
    for term in terms:
        aliases = equivalent_terms(term) or [term]
        if any(contains_term(haystack, alias) for alias in aliases):
            return 1.0
    return 0.0


def _salary_pref(snap: JobSnapshot, low: object, high: object) -> float | None:
    if snap.salary_unit not in {None, "month_k"}:
        return None
    pref_low = _as_float(low)
    pref_high = _as_float(high)
    job_low = None if snap.salary_min is None else float(snap.salary_min)
    job_high = None if snap.salary_max is None else float(snap.salary_max)
    if pref_low is None or pref_high is None or job_low is None or job_high is None:
        return None
    if pref_high < pref_low or job_high < job_low:
        return None
    overlap = min(pref_high, job_high) - max(pref_low, job_low)
    if overlap < 0:
        overlap = 0.0
    width = pref_high - pref_low
    if width == 0:
        return 1.0 if job_low <= pref_low <= job_high else 0.0
    ratio = overlap / width
    if ratio > 1.0:
        return 1.0
    return ratio


def _experience(snap: JobSnapshot, facts: list[Fact]) -> float | None:
    parts: list[float] = []
    job_years = _job_years(snap.experience_text)
    profile_years = _profile_years(facts)
    if job_years is not None and profile_years is not None:
        parts.append(1.0 / (1.0 + abs(job_years - profile_years)))
    job_degree = _degree_rank(snap.degree_text)
    profile_degree = _profile_degree(facts)
    if job_degree is not None and profile_degree is not None:
        parts.append(1.0 / (1.0 + abs(job_degree - profile_degree)))
    if not parts:
        return None
    return sum(parts) / len(parts)


def _supporting_fact(requirement: Requirement, facts: list[Fact]) -> Fact | None:
    needles = list(requirement.normalized.skills) or [requirement.text]
    for fact in facts:
        for needle in needles:
            if _fact_supports(fact, needle):
                return fact
    return None


def _fact_supports(fact: Fact, needle: str) -> bool:
    structured = fact.structured.skills if fact.structured and fact.structured.skills else []
    for skill in structured:
        if skills_equivalent(skill, needle) or contains_term(needle, skill):
            return True
    haystack = fact.statement or ""
    aliases = equivalent_terms(needle) or [needle]
    return any(contains_term(haystack, alias) for alias in aliases)


def _job_years(text: str | None) -> int | None:
    if text is None or not text.strip() or "不限" in text:
        return None
    match = _EXPERIENCE.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _profile_years(facts: list[Fact]) -> int | None:
    spans: list[int] = []
    for fact in facts:
        if fact.category not in {"work", "project"} or fact.structured is None:
            continue
        start = _year(fact.structured.start)
        end = 2026 if fact.structured.end in {"至今", "现在"} else _year(fact.structured.end)
        if start is None or end is None or end < start:
            continue
        spans.append(end - start)
    if not spans:
        return None
    return max(spans)


def _profile_degree(facts: list[Fact]) -> int | None:
    best: int | None = None
    for fact in facts:
        if fact.category != "education":
            continue
        pieces = [fact.statement]
        if fact.structured is not None:
            pieces.extend([fact.structured.role or "", fact.structured.org or ""])
        rank = _degree_rank(" ".join(pieces))
        if rank is not None and (best is None or rank > best):
            best = rank
    return best


def _degree_rank(text: str | None) -> int | None:
    if not text:
        return None
    for label, rank in _DEGREE_RANKS:
        if label in text:
            return rank
    return None


def _year(value: str | None) -> int | None:
    if value is None:
        return None
    match = _YEAR.search(value)
    if match is None:
        return None
    return int(match.group(1))


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
