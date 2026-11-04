"""材料文本的确定性比对。不访问模型，也不改写事实。"""

from __future__ import annotations

import re

from jobbot.contracts import Fact

CONTRIBUTION_RANK = {
    "support": 0,
    "participate": 1,
    "own": 2,
    "push": 3,
    "lead": 4,
}
_CONTRIBUTION_ORDER = ("support", "participate", "own", "push", "lead")
assert tuple(CONTRIBUTION_RANK) == _CONTRIBUTION_ORDER
assert [CONTRIBUTION_RANK[name] for name in _CONTRIBUTION_ORDER] == [0, 1, 2, 3, 4]

VERB_PHRASES = (
    ("独立负责", "lead"),
    ("主导", "lead"),
    ("带领", "lead"),
    ("牵头", "lead"),
    ("推动", "push"),
    ("推进", "push"),
    ("负责", "own"),
    ("参与", "participate"),
    ("协助", "support"),
    ("配合", "support"),
    ("支持", "support"),
)

_DATE_TOKEN = r"(?:\d{4}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?|至今)"
DATE_PATTERN = re.compile(_DATE_TOKEN)
RANGE_PATTERN = re.compile(rf"({_DATE_TOKEN})\s*(?:至|到|—|–|~)\s*({_DATE_TOKEN})")
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?%?(?![A-Za-z0-9])")
ORG_PATTERN = re.compile(r"[\u4e00-\u9fff]{1,20}(?:公司|集团|大学|学院|银行|医院|研究所)")
ENGLISH_SKILL = re.compile(r"[A-Za-z][A-Za-z0-9+#.]{1,}")
CHINESE_SKILL = re.compile(
    r"(?:熟悉|掌握|精通|使用|运用)([\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9+#.]{1,})"
)

RATIONALE_LIMIT = 60
QUESTION_LIMIT = 5


def clip_rationale(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) > RATIONALE_LIMIT:
        compact = compact[:RATIONALE_LIMIT]
    assert len(compact) <= RATIONALE_LIMIT
    return compact


def normalize_date(token: str) -> str:
    if token == "至今":
        return "至今"
    cleaned = token.replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = cleaned.replace("/", "-").replace(".", "-").strip("-")
    parts = [part for part in cleaned.split("-") if part]
    if not parts:
        return token
    normalized = [parts[0]]
    for part in parts[1:]:
        if part.isdigit():
            normalized.append(f"{int(part):02d}")
        else:
            normalized.append(part)
    return "-".join(normalized)


def extract_dates(text: str) -> set[str]:
    return {normalize_date(match.group(0)) for match in DATE_PATTERN.finditer(text or "")}


def blank_dates(text: str) -> str:
    chars = list(text or "")
    for match in DATE_PATTERN.finditer(text or ""):
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def normalize_number(raw: str) -> str:
    percent = raw.endswith("%")
    body = raw[:-1] if percent else raw
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    return body + ("%" if percent else "")


def extract_numbers(text: str) -> set[str]:
    return {normalize_number(match.group(0)) for match in NUMBER_PATTERN.finditer(blank_dates(text or ""))}


def extract_orgs(text: str) -> set[str]:
    return set(ORG_PATTERN.findall(text or ""))


def extract_skills(text: str) -> set[str]:
    found = {token.casefold() for token in ENGLISH_SKILL.findall(text or "")}
    found.update(token.casefold() for token in CHINESE_SKILL.findall(text or ""))
    return found


def fact_skill_names(fact: Fact) -> set[str]:
    names = extract_skills(fact.statement)
    structured = fact.structured
    if structured and structured.skills:
        names.update(skill.casefold() for skill in structured.skills if skill.strip())
    return names


def fact_dates(fact: Fact) -> set[str]:
    found = extract_dates(fact.statement)
    structured = fact.structured
    if structured is not None:
        if structured.start:
            found |= extract_dates(structured.start)
        if structured.end:
            found |= extract_dates(structured.end)
        for metric in structured.metrics or []:
            found |= extract_dates(metric.text)
    return found


def fact_numbers(fact: Fact) -> set[str]:
    found = extract_numbers(fact.statement)
    structured = fact.structured
    if structured is None:
        return found
    for metric in structured.metrics or []:
        found |= extract_numbers(metric.text)
        if metric.value is not None:
            found.add(normalize_number(str(metric.value)))
    return found


def fact_orgs(fact: Fact) -> set[str]:
    found = extract_orgs(fact.statement)
    structured = fact.structured
    if structured and structured.org:
        found.add(structured.org)
        found |= extract_orgs(structured.org)
    return found


def strongest_verb(text: str) -> str | None:
    occupied = [False] * len(text)
    ranks: list[int] = []
    phrases = sorted(VERB_PHRASES, key=lambda item: len(item[0]), reverse=True)
    for phrase, level in phrases:
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            end = index + len(phrase)
            if not any(occupied[index:end]):
                for cursor in range(index, end):
                    occupied[cursor] = True
                ranks.append(CONTRIBUTION_RANK[level])
            start = end
    if not ranks:
        return None
    best = max(ranks)
    for name, rank in CONTRIBUTION_RANK.items():
        if rank == best:
            return name
    return None


def contribution_ceiling(facts: list[Fact]) -> int | None:
    ranks = [
        CONTRIBUTION_RANK[fact.contribution]
        for fact in facts
        if fact.contribution in CONTRIBUTION_RANK
    ]
    if not ranks:
        return None
    return min(ranks)


def timeline_reversed(text: str, facts: list[Fact]) -> bool:
    for fact in facts:
        structured = fact.structured
        if structured is None or not structured.start or not structured.end:
            continue
        start = normalize_date(structured.start)
        end = normalize_date(structured.end)
        if start == end:
            continue
        for match in RANGE_PATTERN.finditer(text):
            left = normalize_date(match.group(1))
            right = normalize_date(match.group(2))
            if left == end and right == start:
                return True
    return False
