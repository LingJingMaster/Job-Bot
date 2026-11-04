"""把详情 JSON 映射成草稿。纯函数，不访问数据库，也不解析薪资结构。"""

from __future__ import annotations

import html
from typing import Any

from pydantic import ValidationError

from jobbot.contracts import JobSnapshotDraft, ParseError


def parse_detail(raw: dict[str, Any]) -> JobSnapshotDraft:
    """按 docs/03 §6 映射字段。缺 jobName 或 postDescription 时抛 ParseError。"""
    if not isinstance(raw, dict):
        raise ParseError(
            "详情响应不是对象。没有生成快照，也没有入库。",
            saved=False,
            next_step="提供含 zpData 的 JSON 对象。",
        )
    body = _response_body(raw)
    zp_data = body.get("zpData") if isinstance(body, dict) else None
    if not isinstance(zp_data, dict) or len(zp_data) == 0:
        raise ParseError(
            "详情的 zpData 为空。没有生成快照，也没有入库。",
            saved=False,
            next_step="换一条含 zpData 的详情。",
        )
    job_info = zp_data.get("jobInfo")
    if not isinstance(job_info, dict):
        job_info = {}
    brand = zp_data.get("brandComInfo")
    if not isinstance(brand, dict):
        brand = {}
    boss_info = zp_data.get("bossInfo")
    if not isinstance(boss_info, dict):
        boss_info = {}

    title = _plain_text(job_info.get("jobName"))
    description = _plain_text(job_info.get("postDescription"))
    missing = [
        name
        for name, value in (("jobName", title), ("postDescription", description))
        if value is None
    ]
    if missing:
        joined = "、".join(missing)
        raise ParseError(
            f"详情缺少 {joined}。没有生成快照，也没有入库。",
            saved=False,
            next_step="换一条同时含岗位名和职位描述的详情，或改用粘贴导入。",
        )

    try:
        return JobSnapshotDraft(
            platform="boss",
            source="boss",
            external_id=_plain_text(job_info.get("encryptId")),
            url=None,
            raw_path=None,
            title=title,
            company=_plain_text(brand.get("brandName")),
            city=_plain_text(job_info.get("locationName")),
            district=None,
            salary_text=_plain_text(job_info.get("salaryDesc")),
            salary_min=None,
            salary_max=None,
            salary_unit=None,
            salary_months=None,
            experience_text=_plain_text(job_info.get("experienceName")),
            degree_text=_plain_text(job_info.get("degreeName")),
            description=description,
            hr_title=_plain_text(boss_info.get("title")),
            hr_active_text=_plain_text(boss_info.get("activeTimeDesc")),
            availability="unknown",
        )
    except ValidationError as exc:
        raise ParseError(
            f"详情文本无法保存：{_validation_message(exc)}。没有入库。",
            saved=False,
            next_step="缩短职位描述后再试。原文没有被截断。",
        ) from exc


def _response_body(raw: dict[str, Any]) -> dict[str, Any]:
    if "zpData" in raw or "jobInfo" in raw:
        return raw
    nested = raw.get("body")
    if isinstance(nested, dict):
        return nested
    return raw


def _plain_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    decoded = html.unescape(value).strip()
    if decoded == "":
        return None
    return decoded


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "字段校验未通过"
    message = errors[0].get("msg")
    if isinstance(message, str) and message.strip():
        return message
    return "字段校验未通过"
