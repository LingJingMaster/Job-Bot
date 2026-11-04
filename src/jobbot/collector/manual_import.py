"""粘贴 JD 或导入 JSON。来源固定为 manual，不启动浏览器。"""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import ValidationError

from jobbot.contracts import MAX_TEXT_CHARS, JobSnapshot, JobSnapshotDraft, ParseError

from jobbot.collector.database import open_database
from jobbot.collector.detail_parser import _plain_text, parse_detail
from jobbot.collector.store import save_draft


def import_manual(
    text: str | None = None,
    json_obj: dict[str, Any] | None = None,
    *,
    connection: sqlite3.Connection | None = None,
) -> JobSnapshot:
    draft_text = text.strip() if isinstance(text, str) else ""
    if draft_text and json_obj is not None:
        raise ParseError(
            "同时提供了职位描述和 JSON。请只保留一种。数据未保存。",
            saved=False,
            next_step="清空其中一种后再导入。",
        )
    if json_obj is None and not draft_text:
        raise ParseError(
            "没有可导入的内容。请粘贴职位描述，或提供 JSON 对象。数据未保存。",
            saved=False,
            next_step="粘贴 JD 或粘贴 JSON 后再导入。",
        )
    if json_obj is not None:
        draft = _draft_from_json(json_obj)
    else:
        if len(draft_text) > MAX_TEXT_CHARS:
            raise ParseError(
                f"职位描述长度为 {len(draft_text)}，超过 {MAX_TEXT_CHARS} 字符。数据未保存，原文没有被截断。",
                saved=False,
                next_step="缩短文本后再导入。",
            )
        description = _plain_text(draft_text)
        if description is None:
            raise ParseError(
                "职位描述是空的。数据未保存。",
                saved=False,
                next_step="粘贴含正文的 JD。",
            )
        draft = _manual_draft(description=description)
    draft = draft.model_copy(update={"platform": "manual", "source": "manual"})
    with open_database(connection) as db:
        _job, snapshot, _created = save_draft(db, draft, run_id=None)
        return snapshot


def _draft_from_json(json_obj: dict[str, Any]):
    if not isinstance(json_obj, dict) or len(json_obj) == 0:
        raise ParseError(
            "JSON 为空或不是对象。数据未保存。",
            saved=False,
            next_step="提供含职位描述的 JSON 对象。",
        )
    _reject_failed_envelope(json_obj)
    if _is_detail_payload(json_obj):
        return parse_detail(json_obj)
    title = _plain_text(json_obj.get("title")) or _plain_text(json_obj.get("jobName"))
    description = _plain_text(json_obj.get("description")) or _plain_text(json_obj.get("postDescription"))
    if description is None:
        raise ParseError(
            "JSON 里没有职位描述。数据未保存。",
            saved=False,
            next_step="在 description 或 postDescription 中放入职位描述。",
        )
    company = _plain_text(json_obj.get("company")) or _plain_text(json_obj.get("brandName"))
    city = _plain_text(json_obj.get("city")) or _plain_text(json_obj.get("locationName"))
    salary_text = _plain_text(json_obj.get("salary_text")) or _plain_text(json_obj.get("salaryDesc"))
    experience_text = _plain_text(json_obj.get("experience_text")) or _plain_text(json_obj.get("experienceName"))
    degree_text = _plain_text(json_obj.get("degree_text")) or _plain_text(json_obj.get("degreeName"))
    external_id = _plain_text(json_obj.get("external_id")) or _plain_text(json_obj.get("encryptId"))
    hr_title = _plain_text(json_obj.get("hr_title"))
    hr_active_text = _plain_text(json_obj.get("hr_active_text"))
    boss_info = json_obj.get("bossInfo")
    if isinstance(boss_info, dict):
        hr_title = hr_title or _plain_text(boss_info.get("title"))
        hr_active_text = hr_active_text or _plain_text(boss_info.get("activeTimeDesc"))
    return _manual_draft(
        title=title,
        description=description,
        company=company,
        city=city,
        salary_text=salary_text,
        experience_text=experience_text,
        degree_text=degree_text,
        external_id=external_id,
        hr_title=hr_title,
        hr_active_text=hr_active_text,
        url=_plain_text(json_obj.get("url")),
    )


def _manual_draft(**fields: Any) -> JobSnapshotDraft:
    try:
        return JobSnapshotDraft(
            platform="manual",
            source="manual",
            availability="unknown",
            salary_min=None,
            salary_max=None,
            salary_unit=None,
            salary_months=None,
            district=None,
            **fields,
        )
    except ValidationError as exc:
        raise ParseError(
            "这条内容无法保存，原文没有被截断。数据未保存。",
            saved=False,
            next_step="缩短职位描述后再导入。",
        ) from exc


def _is_detail_payload(obj: dict[str, Any]) -> bool:
    if "zpData" in obj or "jobInfo" in obj:
        return True
    nested = obj.get("body")
    return isinstance(nested, dict) and ("zpData" in nested or "jobInfo" in nested)


def _reject_failed_envelope(obj: dict[str, Any]) -> None:
    status = obj.get("http_status")
    if "http_status" in obj and status != 200:
        raise ParseError(
            f"JSON 的 http_status 是 {status}，不是 200。数据未保存。",
            saved=False,
            next_step="只导入 HTTP 200 且业务 code 为 0 的详情。",
        )
    body = obj.get("body") if isinstance(obj.get("body"), dict) else obj
    if isinstance(body, dict) and "code" in body and type(body.get("code")) is int and body.get("code") != 0:
        raise ParseError(
            f"JSON 的业务 code 是 {body.get('code')}，不是 0。数据未保存。",
            saved=False,
            next_step="不要把失败响应当作岗位导入。",
        )
    if isinstance(body, dict) and "code" in body and type(body.get("code")) is not int:
        raise ParseError(
            "JSON 的业务 code 不是整数 0。数据未保存。",
            saved=False,
            next_step="使用 code 为 0 的详情 JSON。",
        )
