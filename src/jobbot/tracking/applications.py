"""手动申请跟踪。

一个岗位只保留一条 application。阶段只由用户修改，每次真正的阶段变更追加一条
application_event。applied 只表示用户声明自己已在平台投递。
本模块不核验投递，也不向任何外部地址发出请求。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, get_args

from pydantic import ValidationError

from jobbot.config import load_config
from jobbot.contracts import (
    MAX_TEXT_CHARS,
    ApplicationStage,
    ContractModel,
    JobBotError,
    MaterialKind,
    MaterialStatus,
    Platform,
)
from jobbot.db.connection import connect, migrate, utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows, update_by_id

STAGE_ORDER: tuple[ApplicationStage, ...] = get_args(ApplicationStage)
STAGE_LABEL: dict[ApplicationStage, str] = {
    "interested": "关注中",
    "preparing": "准备材料",
    "ready": "待自己投递",
    "applied": "已声明投递",
    "communicating": "沟通中",
    "interviewing": "面试中",
    "offer": "已记录 Offer",
    "closed": "已关闭",
}
assert set(STAGE_LABEL) == set(STAGE_ORDER)


class ApplicationRecord(ContractModel):
    id: str
    job_id: str
    snapshot_id: str
    stage: ApplicationStage
    material_id: str | None = None
    note: str | None = None
    closed_reason: str | None = None
    created_at: str
    updated_at: str


class ApplicationEventRecord(ContractModel):
    id: str
    application_id: str
    from_stage: ApplicationStage | None = None
    to_stage: ApplicationStage
    reason: str | None = None
    origin: str
    at: str
    created_at: str
    updated_at: str

    def model_post_init(self, _context: Any) -> None:
        if self.origin != "manual":
            raise ValueError("application_event.origin 只能是 manual")


class StageUpdate(ContractModel):
    application: ApplicationRecord
    event_written: bool


class TrackableJob(ContractModel):
    job_id: str
    snapshot_id: str
    title: str | None = None
    company: str | None = None
    city: str | None = None
    platform: Platform


class MaterialChoice(ContractModel):
    id: str
    job_id: str
    kind: MaterialKind
    revision: int
    status: MaterialStatus


class PageData(ContractModel):
    jobs: list[TrackableJob]
    applications: list[ApplicationRecord]
    events: list[ApplicationEventRecord]
    materials: list[MaterialChoice]


def record_manual_stage(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    snapshot_id: str,
    to_stage: str,
    reason: str,
    closed_reason: str | None = None,
    material_id: str | None = None,
    note: str | None = None,
) -> StageUpdate:
    """把一个岗位记到指定阶段。已有申请则改同一条，不新建第二条。"""
    stage = _require_stage(to_stage)
    note_text = _optional_text(note, "备注")
    material_text = _optional_text(material_id, "材料")
    _require_job_snapshot(connection, job_id, snapshot_id)
    _require_material(connection, job_id, material_text)
    existing = _find_job_application(connection, job_id)
    changing = existing is None or existing["stage"] != stage
    reason_text = _require_reason(reason) if changing else _optional_text(reason, "原因")
    closed_text = _require_closed_reason(closed_reason) if stage == "closed" else None
    if existing is None:
        try:
            application_id = _insert_application(
                connection,
                job_id=job_id,
                snapshot_id=snapshot_id,
                stage=stage,
                material_id=material_text,
                note=note_text,
                closed_reason=closed_text,
            )
        except JobBotError as exc:
            if "application.job_id" not in str(exc):
                raise
            existing = _find_job_application(connection, job_id)
            if existing is None:
                raise
        else:
            assert reason_text is not None
            _insert_event(
                connection,
                application_id=application_id,
                from_stage=None,
                to_stage=stage,
                reason=reason_text,
            )
            _assert_single(connection, job_id)
            return StageUpdate(
                application=_load_application(connection, application_id),
                event_written=True,
            )
    return _update_existing(
        connection,
        existing=existing,
        job_id=job_id,
        stage=stage,
        reason_text=reason_text,
        closed_text=closed_text,
        material_id=material_text,
        note=note_text,
    )


def list_events(connection: sqlite3.Connection, application_id: str) -> list[ApplicationEventRecord]:
    return [event for event in _all_events(connection) if event.application_id == application_id]


def load_page() -> PageData:
    try:
        with open_db() as connection:
            return PageData(
                jobs=list_trackable_jobs(connection),
                applications=[_application_from_row(row) for row in list_rows(connection, "application")],
                events=_all_events(connection),
                materials=_material_choices(connection),
            )
    except ValidationError as exc:
        raise JobBotError(
            "申请跟踪读到无法识别的记录。没有修改申请。",
            saved=False,
            next_step="检查岗位或申请里的枚举字段。",
        ) from exc


def save_manual_stage(
    *,
    job_id: str,
    snapshot_id: str,
    to_stage: str,
    reason: str,
    closed_reason: str | None,
    material_id: str | None,
    note: str | None,
) -> StageUpdate:
    with open_db() as connection:
        return record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage=to_stage,
            reason=reason,
            closed_reason=closed_reason,
            material_id=material_id,
            note=note,
        )


def list_trackable_jobs(connection: sqlite3.Connection) -> list[TrackableJob]:
    jobs = {row["id"]: row for row in list_rows(connection, "job")}
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in list_rows(connection, "job_snapshot"):
        job_id = str(snapshot["job_id"])
        current = latest.get(job_id)
        rank = (snapshot["fetched_at"], snapshot["id"])
        if current is None or rank > (current["fetched_at"], current["id"]):
            latest[job_id] = snapshot
    found: list[TrackableJob] = []
    for job_id, snapshot in latest.items():
        job = jobs.get(job_id)
        if job is None:
            continue
        found.append(
            TrackableJob(
                job_id=job_id,
                snapshot_id=str(snapshot["id"]),
                title=snapshot.get("title"),
                company=snapshot.get("company"),
                city=snapshot.get("city"),
                platform=job["platform"],
            )
        )
    found.sort(key=lambda item: ((item.title or ""), item.company or "", item.job_id))
    return found


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    settings = load_config()
    connection = connect(settings.data_dir / "db" / "jobbot.sqlite")
    try:
        migrate(connection)
        yield connection
    finally:
        connection.close()


def _update_existing(
    connection: sqlite3.Connection,
    *,
    existing: dict[str, Any],
    job_id: str,
    stage: ApplicationStage,
    reason_text: str | None,
    closed_text: str | None,
    material_id: str | None,
    note: str | None,
) -> StageUpdate:
    application_id = str(existing["id"])
    current_stage = existing["stage"]
    if current_stage != stage:
        assert reason_text is not None
        update_by_id(
            connection,
            "application",
            application_id,
            {
                "stage": stage,
                "material_id": material_id,
                "note": note,
                "closed_reason": closed_text,
            },
        )
        _insert_event(
            connection,
            application_id=application_id,
            from_stage=current_stage,
            to_stage=stage,
            reason=reason_text,
        )
        _assert_single(connection, job_id)
        return StageUpdate(application=_load_application(connection, application_id), event_written=True)

    fields: dict[str, Any] = {}
    if _none_if_blank(existing.get("note")) != note:
        fields["note"] = note
    if _none_if_blank(existing.get("material_id")) != material_id:
        fields["material_id"] = material_id
    if stage == "closed" and _none_if_blank(existing.get("closed_reason")) != closed_text:
        fields["closed_reason"] = closed_text
    if fields:
        update_by_id(connection, "application", application_id, fields)
    _assert_single(connection, job_id)
    return StageUpdate(application=_load_application(connection, application_id), event_written=False)


def _insert_application(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    snapshot_id: str,
    stage: ApplicationStage,
    material_id: str | None,
    note: str | None,
    closed_reason: str | None,
) -> str:
    return insert(
        connection,
        "application",
        {
            "job_id": job_id,
            "snapshot_id": snapshot_id,
            "stage": stage,
            "material_id": material_id,
            "note": note,
            "closed_reason": closed_reason,
        },
    )


def _insert_event(
    connection: sqlite3.Connection,
    *,
    application_id: str,
    from_stage: str | None,
    to_stage: str,
    reason: str,
) -> None:
    assert to_stage in STAGE_ORDER
    assert from_stage is None or from_stage in STAGE_ORDER
    assert reason
    payload = {
        "application_id": application_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "reason": reason,
        "origin": "manual",
        "at": utc_now_iso(),
    }
    assert payload["origin"] == "manual"
    insert(connection, "application_event", payload)


def _load_application(connection: sqlite3.Connection, application_id: str) -> ApplicationRecord:
    row = get_by_id(connection, "application", application_id)
    if row is None:
        raise JobBotError(
            "刚写入的申请读不回来。请刷新后再看。",
            saved=True,
            next_step="刷新页面，确认这条申请是否已经在看板里。",
        )
    return _application_from_row(row)


def _application_from_row(row: dict[str, Any]) -> ApplicationRecord:
    payload = dict(row)
    payload["material_id"] = _none_if_blank(payload.get("material_id"))
    payload["note"] = _none_if_blank(payload.get("note"))
    payload["closed_reason"] = _none_if_blank(payload.get("closed_reason"))
    try:
        return ApplicationRecord.model_validate(payload)
    except ValidationError as exc:
        raise JobBotError(
            f"申请 {payload.get('id')} 无法读取。没有继续修改。",
            saved=False,
            next_step="检查这条申请的阶段是否在枚举内。",
        ) from exc


def _event_from_row(row: dict[str, Any]) -> ApplicationEventRecord:
    payload = dict(row)
    payload["from_stage"] = _none_if_blank(payload.get("from_stage"))
    payload["reason"] = _none_if_blank(payload.get("reason"))
    try:
        return ApplicationEventRecord.model_validate(payload)
    except ValidationError as exc:
        raise JobBotError(
            f"阶段事件 {payload.get('id')} 无法读取。没有继续修改。",
            saved=False,
            next_step="检查这条事件的阶段与来源。",
        ) from exc


def _all_events(connection: sqlite3.Connection) -> list[ApplicationEventRecord]:
    rows = list(list_rows(connection, "application_event"))
    rows.sort(key=lambda row: (row["at"] or "", row["created_at"] or "", row["id"]))
    return [_event_from_row(row) for row in rows]


def _material_choices(connection: sqlite3.Connection) -> list[MaterialChoice]:
    snapshots = {row["id"]: row for row in list_rows(connection, "job_snapshot")}
    choices: list[MaterialChoice] = []
    for row in list_rows(connection, "material"):
        snapshot = snapshots.get(row["snapshot_id"])
        if snapshot is None:
            continue
        choices.append(
            MaterialChoice(
                id=str(row["id"]),
                job_id=str(snapshot["job_id"]),
                kind=row["kind"],
                revision=int(row["revision"]),
                status=row["status"],
            )
        )
    choices.sort(key=lambda item: (item.job_id, item.kind, item.revision, item.id))
    return choices


def _find_job_application(connection: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    matches = [row for row in list_rows(connection, "application") if row["job_id"] == job_id]
    assert len(matches) <= 1
    if not matches:
        return None
    return matches[0]


def _assert_single(connection: sqlite3.Connection, job_id: str) -> None:
    matches = [row for row in list_rows(connection, "application") if row["job_id"] == job_id]
    assert len(matches) == 1, job_id


def _require_job_snapshot(connection: sqlite3.Connection, job_id: str, snapshot_id: str) -> None:
    if get_by_id(connection, "job", job_id) is None:
        raise JobBotError(
            "岗位不存在。这次阶段变更没有保存。",
            saved=False,
            next_step="先导入或采集岗位。",
        )
    snapshot = get_by_id(connection, "job_snapshot", snapshot_id)
    if snapshot is None or snapshot["job_id"] != job_id:
        raise JobBotError(
            "岗位快照不存在，或不属于这个岗位。这次阶段变更没有保存。",
            saved=False,
            next_step="重新选择岗位。",
        )


def _require_material(connection: sqlite3.Connection, job_id: str, material_id: str | None) -> None:
    if material_id is None:
        return
    material = get_by_id(connection, "material", material_id)
    if material is None:
        raise JobBotError(
            "材料不存在。这次阶段变更没有保存。",
            saved=False,
            next_step="重新选择材料。",
        )
    snapshot = get_by_id(connection, "job_snapshot", str(material["snapshot_id"]))
    if snapshot is None or snapshot["job_id"] != job_id:
        raise JobBotError(
            "材料不属于这个岗位。这次阶段变更没有保存。",
            saved=False,
            next_step="选择该岗位下的材料。",
        )


def _require_stage(stage: str) -> ApplicationStage:
    if len(stage) > 32 or stage not in STAGE_ORDER:
        raise JobBotError(
            "阶段不在允许的枚举内。这次阶段变更没有保存。",
            saved=False,
            next_step="改用关注中、准备材料、待自己投递、已声明投递、沟通中、面试中、已记录 Offer 或已关闭。",
        )
    return stage  # type: ignore[return-value]


def _require_reason(reason: str | None) -> str:
    text = _optional_text(reason, "原因")
    if text is None:
        raise JobBotError(
            "更改阶段需要填写原因。这次阶段变更没有保存。",
            saved=False,
            next_step="填写原因后再保存。",
        )
    return text


def _require_closed_reason(closed_reason: str | None) -> str:
    text = _optional_text(closed_reason, "关闭原因")
    if text is None:
        raise JobBotError(
            "关闭需要填写关闭原因。这次阶段变更没有保存。",
            saved=False,
            next_step="填写关闭原因后再保存。",
        )
    return text


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_TEXT_CHARS:
        raise JobBotError(
            f"{label}长度为 {len(text)}，超过 {MAX_TEXT_CHARS} 字符。这次阶段变更没有保存。",
            saved=False,
            next_step=f"缩短{label}后再保存。",
        )
    return text


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
