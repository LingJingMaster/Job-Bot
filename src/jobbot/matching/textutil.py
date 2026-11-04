"""排序用文本。去掉电话、邮箱和姓名，不用性别、年龄、婚育。"""

from __future__ import annotations

import re

from jobbot.contracts import Fact

_PHONE = re.compile(r"1[3-9]\d{9}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DEMOGRAPHIC = re.compile(r"性别|年龄|婚育|已婚|未婚|婚姻状况|生育状况|婚否")
_LATIN_EDGE = re.compile(r"[A-Za-z0-9]")


def is_demographic(fact: Fact) -> bool:
    return _DEMOGRAPHIC.search(fact.statement or "") is not None


def redact(text: str, *, name: str | None = None) -> str:
    cleaned = _EMAIL.sub(" ", text)
    cleaned = _PHONE.sub(" ", cleaned)
    if name:
        stripped = name.strip()
        if len(stripped) >= 2 and stripped != "默认档案":
            cleaned = cleaned.replace(stripped, " ")
    return cleaned


def ranking_text(facts: list[Fact], profile_name: str | None = None) -> str:
    parts: list[str] = []
    for fact in facts:
        if fact.status != "confirmed" or is_demographic(fact):
            continue
        statement = (fact.statement or "").strip()
        if statement:
            parts.append(statement)
    return redact("\n".join(parts), name=profile_name).strip()


def contains_term(haystack: str, term: str) -> bool:
    if not term:
        return False
    if _LATIN_EDGE.search(term):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
        return re.search(pattern, haystack, flags=re.IGNORECASE) is not None
    return term in haystack
