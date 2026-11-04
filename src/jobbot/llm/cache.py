"""LLM 磁盘缓存。只保存模型输出，不保存简历、JD 或请求头。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jobbot.db.connection import utc_now_iso


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_cache_key(
    *,
    model_id: str,
    template: str,
    variables: dict[str, Any],
    temperature: int,
    schema_version: str,
) -> str:
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    input_hash = hashlib.sha256(canonical_json(variables).encode("utf-8")).hexdigest()
    material = canonical_json(
        {
            "model_id": model_id,
            "template_hash": template_hash,
            "input_hash": input_hash,
            "temperature": temperature,
            "schema_version": schema_version,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def template_hash(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def input_hash(variables: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(variables).encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, cache_key: str) -> Path:
        return self.directory / f"{cache_key}.json"

    def read(self, cache_key: str) -> str | None:
        path = self.path_for(cache_key)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = payload.get("content")
        if not isinstance(content, str) or content == "":
            return None
        return content

    def write(self, cache_key: str, content: str, *, model_id: str, schema_version: str, temperature: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_key": cache_key,
            "model": model_id,
            "temperature": temperature,
            "schema_version": schema_version,
            "content": content,
            "created_at": utc_now_iso(),
        }
        self.path_for(cache_key).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
