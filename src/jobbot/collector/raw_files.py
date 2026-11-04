"""把原始 JSON 写到 data/raw。写入前去掉招聘者姓名和 encryptUserId。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def redact_personal(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "encryptUserId":
                continue
            if key == "bossInfo" and isinstance(item, dict):
                without_name = {inner_key: inner_value for inner_key, inner_value in item.items() if inner_key != "name"}
                cleaned[key] = redact_personal(without_name)
                continue
            cleaned[key] = redact_personal(item)
        return cleaned
    if isinstance(value, list):
        return [redact_personal(item) for item in value]
    return value


def write_raw_response(directory: Path, index: int, http_status: int | None, body: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "_notice": "原始响应副本。招聘者姓名与 encryptUserId 已去掉。",
        "http_status": http_status,
        "body": redact_personal(body),
    }
    path = directory / f"{index:03d}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
