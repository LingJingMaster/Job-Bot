"""LLM 输出 schema 的版本号。

模型类在 contracts.py。提示词正文尚未由 WP-E 写入，见 prompts/PROVENANCE.md。
"""

from __future__ import annotations

from pydantic import BaseModel

SCHEMA_VERSION = "03-2026-11-04"


def schema_version_of(model: type[BaseModel]) -> str:
    return f"{SCHEMA_VERSION}:{model.__name__}"
