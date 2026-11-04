"""薪资原文解析。无法解析时结构化字段全部为 null，不写 0。"""

from __future__ import annotations

import re
import unicodedata

from jobbot.contracts import Salary

_FULLWIDTH_OFFSET = 0xFEE0
_DASH_CHARS = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "－": "-",
        "~": "-",
        "～": "-",
        "至": "-",
        "到": "-",
        "・": "·",
        "•": "·",
        "．": ".",
    }
)
_MONTH = re.compile(r"^(\d+)-(\d+)(?:k|千)(?:[·.](\d+)薪)?$", re.IGNORECASE)
_DAY = re.compile(r"^(\d+)-(\d+)元/(?:天|日)$")
_HOUR = re.compile(r"^(\d+)-(\d+)元/(?:小时|时)$")
_NEGOTIABLE = re.compile(r"^(?:薪资)?面议$")


def parse_salary(text: str | None) -> Salary:
    if text is None:
        return Salary()
    if text.strip() == "":
        return Salary(salary_text=text)
    if _has_unreadable(text):
        return Salary(salary_text=text)
    compact = re.sub(r"\s+", "", _fold(text))
    negotiable = _NEGOTIABLE.fullmatch(compact)
    if negotiable:
        return Salary(salary_text=text)
    month = _MONTH.fullmatch(compact)
    if month:
        return _range_salary(text, month.group(1), month.group(2), "month_k", month.group(3))
    day = _DAY.fullmatch(compact)
    if day:
        return _range_salary(text, day.group(1), day.group(2), "day_yuan", None)
    hour = _HOUR.fullmatch(compact)
    if hour:
        return _range_salary(text, hour.group(1), hour.group(2), "hour_yuan", None)
    return Salary(salary_text=text)


def _fold(text: str) -> str:
    folded: list[str] = []
    for char in text:
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:
            folded.append(chr(code - _FULLWIDTH_OFFSET))
        elif char == "\u3000":
            folded.append(" ")
        else:
            folded.append(char)
    return "".join(folded).translate(_DASH_CHARS)


def _has_unreadable(text: str) -> bool:
    for char in text:
        if char == "\ufffd":
            return True
        category = unicodedata.category(char)
        if category in {"Co", "Cs", "Cn"}:
            return True
        if category.startswith("C") and char not in "\n\r\t":
            return True
    return False


def _range_salary(
    original: str,
    low_text: str,
    high_text: str,
    unit: str,
    months_text: str | None,
) -> Salary:
    low = int(low_text)
    high = int(high_text)
    months = int(months_text) if months_text is not None else None
    if low <= 0 or high <= 0 or low > high or (months is not None and months <= 0):
        return Salary(salary_text=original)
    return Salary(
        salary_text=original,
        min=low,
        max=high,
        salary_unit=unit,  # type: ignore[arg-type]
        months=months,
    )
