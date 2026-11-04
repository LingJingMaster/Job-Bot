"""发送前脱敏。只替换姓名、大陆手机号、邮箱和住址，保留其余职位描述。"""

from __future__ import annotations

import re
from typing import Any

PHONE_PLACEHOLDER = "[电话已脱敏]"
EMAIL_PLACEHOLDER = "[邮箱已脱敏]"
NAME_PLACEHOLDER = "[姓名已脱敏]"
ADDRESS_PLACEHOLDER = "[住址已脱敏]"

_DIGIT = "0-9０-９"
_PHONE = re.compile(
    rf"(?<![{_DIGIT}])(?:\+?86[\s\-－]?)?[1１][3-9３-９](?:[\s\-－]?[{_DIGIT}]){{9}}(?![{_DIGIT}])"
)
_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![\w.\-])"
)
_NAME = re.compile(r"姓名\s*[:：]\s*[^\s，,。；;、|｜\n]{1,30}")
_LABELED_ADDRESS = re.compile(
    r"(?<!邮箱)(?<!IP)"
    r"(?:家庭住址|现住址|通讯地址|联系地址|住址|地址)"
    r"\s*[:：]\s*[^\n，,。；;]{2,100}"
)
_BARE_ADDRESS = re.compile(
    r"(?:[\u4e00-\u9fff]{2,10}(?:省|自治区))?"
    r"[\u4e00-\u9fff]{2,10}市"
    r"[\u4e00-\u9fff]{1,10}(?:区|县)"
    r"[\u4e00-\u9fffA-Za-z0-9０-９]{0,20}"
    r"(?:路|街|巷|道|大街)"
    r"[\u4e00-\u9fffA-Za-z0-9０-９\-]{0,12}号"
)


def redact_text(text: str) -> str:
    """按行替换。一行里的住址或联系方式去掉后，同一段里的职责句子仍保留。"""
    return "\n".join(_redact_line(line) for line in text.split("\n"))


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted = {key: redact_value(item) for key, item in value.items()}
        assert set(redacted) == set(value)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def _redact_line(line: str) -> str:
    line = _EMAIL.sub(EMAIL_PLACEHOLDER, line)
    line = _PHONE.sub(PHONE_PLACEHOLDER, line)
    line = _NAME.sub(NAME_PLACEHOLDER, line)
    line = _LABELED_ADDRESS.sub(ADDRESS_PLACEHOLDER, line)
    line = _BARE_ADDRESS.sub(ADDRESS_PLACEHOLDER, line)
    return line
