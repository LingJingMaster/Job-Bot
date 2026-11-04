"""材料模块用的数据库读写。路径与 LLM 网关相同：DATA_DIR/db/jobbot.sqlite。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import ValidationError

from jobbot.config import load_config
from jobbot.contracts import (
    Claim,
    Fact,
    JDParse,
    JobBotError,
    JobSnapshot,
    Material,
    ProfileSnapshot,
    Question,
    Requirement,
)
from jobbot.db.connection import connect, migrate
from jobbot.db.repository import get_by_id, list_rows


def database_path():
    return load_config().data_dir / "db" / "jobbot.sqlite"


def exports_dir():
    return load_config().data_dir / "exports"


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    connection = connect(database_path())
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()


def load_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise JobBotError(
                "数据库里的 JSON 无法解析。相关记录未改写。下一步：检查该行的 JSON 字段。",
                saved=False,
                next_step="修正损坏的 JSON 后再试。",
            ) from exc
    return value


def _validation_error(kind: str, row_id: object, exc: ValidationError) -> JobBotError:
    return JobBotError(
        f"{kind} {row_id} 无法按契约读取：{exc}。没有继续这份材料。",
        saved=False,
        next_step="修正该记录后再试。",
    )


def row_to_fact(row: dict[str, Any]) -> Fact:
    payload = dict(row)
    payload["is_estimate"] = bool(payload.get("is_estimate") or 0)
    structured = load_json(payload.get("structured"))
    if isinstance(structured, dict):
        structured.setdefault("skills", None)
        structured.setdefault("metrics", None)
        payload["structured"] = structured
    else:
        payload["structured"] = None
    payload["source_ref"] = load_json(payload.get("source_ref"))
    try:
        return Fact.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error("事实", payload.get("id"), exc) from exc


def row_to_profile_snapshot(row: dict[str, Any]) -> ProfileSnapshot:
    payload = dict(row)
    payload["fact_ids"] = load_json(payload.get("fact_ids")) or []
    try:
        return ProfileSnapshot.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error("档案快照", payload.get("id"), exc) from exc


def row_to_job_snapshot(connection: sqlite3.Connection, row: dict[str, Any]) -> JobSnapshot:
    payload = dict(row)
    job = get_by_id(connection, "job", str(row["job_id"]))
    payload["platform"] = job["platform"] if job else "manual"
    try:
        return JobSnapshot.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error("岗位快照", payload.get("id"), exc) from exc


def row_to_jd_parse(row: dict[str, Any]) -> JDParse:
    payload = dict(row)
    payload["requirements"] = load_json(payload.get("requirements"))
    try:
        return JDParse.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error("JD 解析", payload.get("id"), exc) from exc


def row_to_material(row: dict[str, Any]) -> Material:
    try:
        return Material.model_validate(dict(row))
    except ValidationError as exc:
        raise _validation_error("材料", row.get("id"), exc) from exc


def row_to_claim(row: dict[str, Any]) -> Claim:
    payload = dict(row)
    payload["fact_ids"] = load_json(payload.get("fact_ids"))
    payload["requirement_ids"] = load_json(payload.get("requirement_ids"))
    payload["check_detail"] = load_json(payload.get("check_detail"))
    try:
        return Claim.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error("陈述", payload.get("id"), exc) from exc


def row_to_question(row: dict[str, Any]) -> Question:
    try:
        return Question.model_validate(dict(row))
    except ValidationError as exc:
        raise _validation_error("追问", row.get("id"), exc) from exc


def load_documents(connection: sqlite3.Connection) -> dict[str, str]:
    documents: dict[str, str] = {}
    for row in list_rows(connection, "document"):
        documents[str(row["id"])] = row["text"] or ""
    return documents


def citation_ok(fact: Fact, documents: dict[str, str]) -> bool:
    ref = fact.source_ref
    if ref is None or not ref.excerpt:
        return True
    if not ref.source_id or ref.source_id not in documents:
        return False
    source = documents[ref.source_id]
    if ref.span is not None:
        if len(ref.span) != 2:
            return False
        start, end = ref.span
        if start < 0 or end > len(source) or source[start:end] != ref.excerpt:
            return False
    return ref.excerpt in source


def load_confirmed_or_raise(connection: sqlite3.Connection, profile: ProfileSnapshot) -> list[Fact]:
    if not isinstance(profile, ProfileSnapshot):
        raise JobBotError(
            "draft_material 只接受 ProfileSnapshot。这是编程错误。材料未保存。",
            saved=False,
            next_step="传入冻结后的 ProfileSnapshot。",
        )
    facts: list[Fact] = []
    for fact_id in profile.fact_ids:
        row = get_by_id(connection, "fact", fact_id)
        if row is None:
            raise JobBotError(
                f"draft_material 只接受已确认事实。事实 {fact_id} 不存在，这是编程错误。材料未保存。",
                saved=False,
                next_step="传入只含 confirmed 事实的 ProfileSnapshot。",
            )
        if row["status"] != "confirmed":
            raise JobBotError(
                "draft_material 只接受已确认事实。"
                f"事实 {fact_id} 的状态是 {row['status']}，这是编程错误。材料未保存。",
                saved=False,
                next_step="传入只含 confirmed 事实的 ProfileSnapshot。",
            )
        fact = row_to_fact(row)
        if fact.profile_id != profile.profile_id:
            raise JobBotError(
                f"事实 {fact_id} 不属于档案 {profile.profile_id}。这是编程错误。材料未保存。",
                saved=False,
                next_step="使用同一档案冻结出的 ProfileSnapshot。",
            )
        facts.append(fact)
    return facts


def require_confirmed_facts(profile: ProfileSnapshot) -> list[Fact]:
    with connect_db() as connection:
        return load_confirmed_or_raise(connection, profile)


def load_facts(fact_ids: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    with connect_db() as connection:
        for fact_id in fact_ids:
            row = get_by_id(connection, "fact", fact_id)
            if row is None:
                continue
            facts.append(row_to_fact(row))
    return facts


def list_profile_snapshots() -> list[ProfileSnapshot]:
    with connect_db() as connection:
        rows = list_rows(connection, "profile_snapshot")
    snapshots = [row_to_profile_snapshot(row) for row in rows]
    snapshots.sort(key=lambda item: (item.revision, item.created_at or "", item.id))
    return snapshots


def list_job_snapshots() -> list[JobSnapshot]:
    with connect_db() as connection:
        snapshots = [
            row_to_job_snapshot(connection, row) for row in list_rows(connection, "job_snapshot")
        ]
    snapshots.sort(key=lambda item: (item.fetched_at, item.id))
    return snapshots


def load_profile_snapshot(snapshot_id: str) -> ProfileSnapshot:
    with connect_db() as connection:
        row = get_by_id(connection, "profile_snapshot", snapshot_id)
    if row is None:
        raise JobBotError(
            "档案快照不存在。材料未改写。下一步：先冻结档案。",
            saved=False,
            next_step="重新选择档案快照。",
        )
    return row_to_profile_snapshot(row)


def load_job_snapshot(snapshot_id: str) -> JobSnapshot:
    with connect_db() as connection:
        row = get_by_id(connection, "job_snapshot", snapshot_id)
        if row is None:
            raise JobBotError(
                "岗位快照不存在。材料未改写。下一步：先选择岗位。",
                saved=False,
                next_step="重新选择岗位快照。",
            )
        return row_to_job_snapshot(connection, row)


def load_material(material_id: str) -> Material:
    with connect_db() as connection:
        row = get_by_id(connection, "material", material_id)
    if row is None:
        raise JobBotError(
            "材料不存在。没有继续该操作。下一步：先生成草稿。",
            saved=False,
            next_step="生成草稿后再试。",
        )
    return row_to_material(row)


def list_materials_for(profile_snapshot_id: str, snapshot_id: str) -> list[Material]:
    with connect_db() as connection:
        rows = [
            row
            for row in list_rows(connection, "material")
            if row["profile_snapshot_id"] == profile_snapshot_id and row["snapshot_id"] == snapshot_id
        ]
    materials = [row_to_material(row) for row in rows]
    materials.sort(key=lambda item: (item.revision, item.created_at or "", item.id))
    return materials


def list_claims(material_id: str) -> list[Claim]:
    with connect_db() as connection:
        rows = [row for row in list_rows(connection, "claim") if row["material_id"] == material_id]
    claims = [row_to_claim(row) for row in rows]
    claims.sort(key=lambda item: (item.span_start, item.id or ""))
    return claims


def list_questions(material_id: str) -> list[Question]:
    with connect_db() as connection:
        rows = [row for row in list_rows(connection, "question") if row["material_id"] == material_id]
    questions = [row_to_question(row) for row in rows]
    questions.sort(key=lambda item: (item.created_at or "", item.id or ""))
    return questions


def latest_jd_parse(snapshot_id: str) -> JDParse | None:
    with connect_db() as connection:
        rows = [
            row
            for row in list_rows(connection, "jd_parse")
            if row["snapshot_id"] == snapshot_id and row["status"] == "ok"
        ]
    if not rows:
        return None
    rows.sort(key=lambda row: (row["created_at"] or "", row["id"]))
    return row_to_jd_parse(rows[-1])


def load_requirement_index() -> dict[str, Requirement]:
    index: dict[str, Requirement] = {}
    with connect_db() as connection:
        rows = [row for row in list_rows(connection, "jd_parse") if row["status"] == "ok"]
    rows.sort(key=lambda row: (row["created_at"] or "", row["id"]))
    for row in rows:
        parsed = row_to_jd_parse(row)
        for requirement in parsed.requirements or []:
            index[requirement.id] = requirement
    return index
