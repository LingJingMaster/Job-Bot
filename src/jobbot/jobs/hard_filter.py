"""硬条件。未知字段按该条 unknown_policy，默认 needs_review。"""

from __future__ import annotations

import re
from typing import Any

from jobbot.contracts import Fact, HardCheck, HardConstraint, JobSnapshot, Preference
from jobbot.jobs.aliases import canonical_city, equivalent_terms

_DEGREE_RANKS: tuple[tuple[str, int], ...] = (
    ("博士", 6),
    ("硕士", 5),
    ("本科", 4),
    ("大专", 3),
    ("专科", 3),
    ("中技", 2),
    ("中专", 2),
    ("高中", 2),
    ("初中", 1),
)
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CURRENT_YEAR = 2026


def hard_filter(snapshot: JobSnapshot, pref: Preference, facts: list[Fact]) -> HardCheck:
    if pref.hard is None:
        return HardCheck(
            hard_result="needs_review",
            hard_reasons=["硬条件未提供，按未知处理。"],
            unknown_fields=["hard"],
        )
    if not pref.hard:
        return HardCheck(hard_result="pass", hard_reasons=["没有硬条件。"], unknown_fields=[])
    reasons: list[str] = []
    unknown_fields: list[str] = []
    states: list[str] = []
    for constraint in pref.hard:
        state, reason, unknown_field = _check(constraint, snapshot, facts)
        states.append(state)
        reasons.append(reason)
        if unknown_field and unknown_field not in unknown_fields:
            unknown_fields.append(unknown_field)
    if "fail" in states:
        result = "fail"
    elif "needs_review" in states:
        result = "needs_review"
    else:
        result = "pass"
    return HardCheck(hard_result=result, hard_reasons=reasons, unknown_fields=unknown_fields)  # type: ignore[arg-type]


def _check(constraint: HardConstraint, snapshot: JobSnapshot, facts: list[Fact]) -> tuple[str, str, str | None]:
    if constraint.field == "city":
        return _city(constraint, snapshot)
    if constraint.field == "degree":
        return _degree(constraint, snapshot, facts)
    if constraint.field == "experience_years":
        return _experience(constraint, snapshot, facts)
    if constraint.field == "salary_min":
        return _salary_min(constraint, snapshot)
    if constraint.field == "salary_max":
        return _salary_max(constraint, snapshot)
    if constraint.field == "keyword_exclude":
        return _exclude(constraint, snapshot)
    return _unknown(constraint, constraint.field, "这条硬条件的字段无法识别。")


def _city(constraint: HardConstraint, snapshot: JobSnapshot) -> tuple[str, str, str | None]:
    actual = canonical_city(snapshot.city)
    if actual is None:
        return _unknown(constraint, "city", "岗位城市未知。")
    if constraint.operator == "eq":
        expected = canonical_city(str(constraint.value)) if constraint.value is not None else None
        if expected is None:
            return _unknown(constraint, "city", "偏好城市为空。")
        if actual == expected:
            return "pass", f"城市通过：{actual}。", None
        return "fail", f"城市不匹配：岗位为 {actual}，偏好为 {expected}。", None
    if constraint.operator == "in":
        values = constraint.value if isinstance(constraint.value, list) else [constraint.value]
        expected = [canonical_city(str(item)) for item in values]
        expected = [item for item in expected if item]
        if not expected:
            return _unknown(constraint, "city", "偏好城市列表为空。")
        if actual in expected:
            return "pass", f"城市通过：{actual} 在偏好 { '、'.join(expected) } 中。", None
        return "fail", f"城市不匹配：岗位为 {actual}，偏好为 {'、'.join(expected)}。", None
    return _unknown(constraint, "city", "城市条件的运算符无法执行。")


def _degree(constraint: HardConstraint, snapshot: JobSnapshot, facts: list[Fact]) -> tuple[str, str, str | None]:
    job_rank = _degree_rank(snapshot.degree_text)
    if job_rank is None:
        return _unknown(constraint, "degree", "岗位学历未知或无法识别。")
    expected_rank, expected_label = _expected_degree(constraint, facts)
    if expected_rank is None:
        return _unknown(constraint, "degree", "学历偏好未知，已确认教育事实里也没有学历。")
    if constraint.operator == "gte":
        matched = job_rank >= expected_rank
    elif constraint.operator == "lte":
        matched = job_rank <= expected_rank
    elif constraint.operator == "eq":
        matched = job_rank == expected_rank
    elif constraint.operator == "in":
        labels = constraint.value if isinstance(constraint.value, list) else [constraint.value]
        matched = any(_degree_rank(str(label)) == job_rank for label in labels)
    else:
        return _unknown(constraint, "degree", "学历条件的运算符无法执行。")
    job_label = _degree_label(snapshot.degree_text) or snapshot.degree_text
    if matched:
        return "pass", f"学历通过：岗位为 {job_label}，偏好为 {expected_label}。", None
    return "fail", f"学历不满足：岗位为 {job_label}，偏好为 {expected_label}。", None


def _experience(constraint: HardConstraint, snapshot: JobSnapshot, facts: list[Fact]) -> tuple[str, str, str | None]:
    job_years = _experience_years(snapshot.experience_text)
    if job_years is None:
        return _unknown(constraint, "experience_years", "岗位经验年限未知。")
    value = _as_int(constraint.value)
    if value is None:
        value = _years_from_facts(facts)
    if value is None:
        return _unknown(constraint, "experience_years", "经验年限偏好未知，已确认经历里也算不出年限。")
    if constraint.operator == "gte":
        matched = job_years >= value
    elif constraint.operator == "lte":
        matched = job_years <= value
    elif constraint.operator == "eq":
        matched = job_years == value
    else:
        return _unknown(constraint, "experience_years", "经验年限的运算符无法执行。")
    if matched:
        return "pass", f"经验年限通过：岗位要求 {job_years} 年，偏好为 {value} 年。", None
    return "fail", f"经验年限不满足：岗位要求 {job_years} 年，偏好为 {value} 年。", None


def _salary_min(constraint: HardConstraint, snapshot: JobSnapshot) -> tuple[str, str, str | None]:
    value = _as_int(constraint.value)
    if value is None:
        return _unknown(constraint, "salary_min", "薪资下限偏好无法读取。")
    if snapshot.salary_unit not in {None, "month_k"} or (
        snapshot.salary_unit is None and (snapshot.salary_min is not None or snapshot.salary_max is not None)
    ):
        unit = snapshot.salary_unit or "未标注"
        return _unknown(constraint, "salary_min", f"岗位薪资单位是 {unit}，没有按千元/月换算。")
    if snapshot.salary_min is None and snapshot.salary_max is None:
        return _unknown(constraint, "salary_min", "岗位薪资未知。")
    if snapshot.salary_max is not None and snapshot.salary_max < value:
        return "fail", f"薪资下限不满足：岗位最高 {snapshot.salary_max} 千元/月，低于偏好 {value}。", None
    if snapshot.salary_min is not None and snapshot.salary_min >= value:
        return "pass", f"薪资下限通过：岗位不低于 {snapshot.salary_min} 千元/月，偏好 {value}。", None
    if snapshot.salary_max is not None and snapshot.salary_max >= value:
        return "pass", f"薪资下限通过：岗位最高 {snapshot.salary_max} 千元/月，达到偏好 {value}。", None
    return _unknown(constraint, "salary_min", "只知道岗位薪资下限，无法判断是否达到期望。")


def _salary_max(constraint: HardConstraint, snapshot: JobSnapshot) -> tuple[str, str, str | None]:
    value = _as_int(constraint.value)
    if value is None:
        return _unknown(constraint, "salary_max", "薪资上限偏好无法读取。")
    if snapshot.salary_unit not in {None, "month_k"}:
        return _unknown(constraint, "salary_max", "岗位薪资单位与千元/月不一致，没有换算。")
    if snapshot.salary_min is None:
        return _unknown(constraint, "salary_max", "岗位薪资下限未知，无法和期望上限比较。")
    if snapshot.salary_min > value:
        return "fail", f"薪资上限不满足：岗位至少 {snapshot.salary_min} 千元/月，高于偏好 {value}。", None
    return "pass", f"薪资上限通过：岗位至少 {snapshot.salary_min} 千元/月，未高于偏好 {value}。", None


def _exclude(constraint: HardConstraint, snapshot: JobSnapshot) -> tuple[str, str, str | None]:
    raw_terms = constraint.value if isinstance(constraint.value, list) else [constraint.value]
    terms = [str(term).strip() for term in raw_terms if str(term).strip()]
    if not terms:
        return "pass", "排除词为空。", None
    haystack = "\n".join(
        part for part in (snapshot.title, snapshot.company, snapshot.description) if part
    )
    if not haystack.strip():
        return _unknown(constraint, "keyword_exclude", "岗位文本为空，无法检查排除词。")
    hits: list[str] = []
    for term in terms:
        aliases = equivalent_terms(term) or [term]
        if any(_contains_term(haystack, alias) for alias in aliases):
            hits.append(term)
    if hits:
        return "fail", f"排除词命中：{'、'.join(hits)}。", None
    return "pass", "排除词没有命中。", None


def _unknown(constraint: HardConstraint, field: str, reason: str) -> tuple[str, str, str | None]:
    if constraint.unknown_policy == "pass":
        return "pass", f"{reason}按策略记为通过。", field
    if constraint.unknown_policy == "fail":
        return "fail", f"{reason}按策略记为不通过。", field
    return "needs_review", f"{reason}按策略记为需复核。", field


def _expected_degree(constraint: HardConstraint, facts: list[Fact]) -> tuple[int | None, str]:
    if constraint.operator == "in":
        labels = constraint.value if isinstance(constraint.value, list) else [constraint.value]
        ranks = [( _degree_rank(str(label)), str(label)) for label in labels]
        usable = [(rank, label) for rank, label in ranks if rank is not None]
        if not usable:
            return None, "未识别"
        return usable[0]
    if constraint.value is not None:
        label = str(constraint.value)
        return _degree_rank(label), _degree_label(label) or label
    label = _degree_label_from_facts(facts)
    if label is None:
        return None, "未知"
    return _degree_rank(label), label


def _degree_rank(text: str | None) -> int | None:
    if not text:
        return None
    for label, rank in _DEGREE_RANKS:
        if label in text:
            return rank
    return None


def _degree_label(text: str | None) -> str | None:
    if not text:
        return None
    for label, _rank in _DEGREE_RANKS:
        if label in text:
            return label
    return None


def _degree_label_from_facts(facts: list[Fact]) -> str | None:
    best_label = None
    best_rank = -1
    for fact in facts:
        if fact.status != "confirmed" or fact.category != "education":
            continue
        pieces = [fact.statement]
        if fact.structured is not None:
            pieces.extend([fact.structured.role or "", fact.structured.org or ""])
        blob = " ".join(pieces)
        rank = _degree_rank(blob)
        label = _degree_label(blob)
        if rank is not None and label is not None and rank > best_rank:
            best_rank = rank
            best_label = label
    return best_label


def _experience_years(text: str | None) -> int | None:
    if text is None or not text.strip() or "不限" in text:
        return None
    if "应届" in text or "在校" in text:
        return 0
    within = re.search(r"(\d+)\s*年以内", text)
    if within:
        return 0
    within_cn = re.search(r"([一二两三四五六七八九十])\s*年以内", text)
    if within_cn:
        return 0
    span = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*年", text)
    if span:
        return int(span.group(1))
    above = re.search(r"(\d+)\s*年以上", text)
    if above:
        return int(above.group(1))
    above_cn = re.search(r"([一二两三四五六七八九十])\s*年以上", text)
    if above_cn:
        return _CN_DIGITS[above_cn.group(1)]
    exact = re.search(r"(\d+)\s*年", text)
    if exact:
        return int(exact.group(1))
    exact_cn = re.search(r"([一二两三四五六七八九十])\s*年", text)
    if exact_cn:
        return _CN_DIGITS[exact_cn.group(1)]
    return None


def _years_from_facts(facts: list[Fact]) -> int | None:
    spans: list[int] = []
    for fact in facts:
        if fact.status != "confirmed" or fact.category not in {"work", "project"} or fact.structured is None:
            continue
        start = _year_number(fact.structured.start)
        if fact.structured.end in {"至今", "现在"}:
            end = _CURRENT_YEAR
        else:
            end = _year_number(fact.structured.end)
        if start is None or end is None or end < start:
            continue
        spans.append(end - start)
    if not spans:
        return None
    return max(spans)


def _year_number(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"(\d{4})", value)
    if match is None:
        return None
    return int(match.group(1))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _contains_term(haystack: str, term: str) -> bool:
    if re.search(r"[A-Za-z0-9]", term):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
        return re.search(pattern, haystack, flags=re.IGNORECASE) is not None
    return term in haystack
