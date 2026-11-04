"""T19：手动改阶段、纠正、关闭、重开。不核验投递，不发外部请求。"""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import httpx
import pytest

from jobbot.config import ENV_KEYS
from jobbot.contracts import JobBotError
from jobbot.db.connection import connect, migrate
from jobbot.db.repository import insert, list_rows
from jobbot.tracking.applications import (
    STAGE_LABEL,
    STAGE_ORDER,
    ApplicationEventRecord,
    list_events,
    record_manual_stage,
    save_manual_stage,
)

ROOT = Path(__file__).resolve().parents[1]
TRACKING_SOURCES = (
    ROOT / "src" / "jobbot" / "tracking" / "applications.py",
    ROOT / "src" / "jobbot" / "tracking" / "__init__.py",
    ROOT / "app" / "views" / "tracking.py",
)
OUTBOUND_MARKERS = (
    "httpx",
    "requests",
    "urllib",
    "socket",
    "http.client",
    "startchat",
    "playwright",
    "aiohttp",
)


def _prepare_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = {key: "" for key in ENV_KEYS}
    values.update(
        {
            "DATA_DIR": str(tmp_path),
            "LLM_MODE": "live",
            "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5",
            "COLLECT_MAX_JOBS": "30",
            "COLLECT_MAX_MINUTES": "20",
            "TASK_BUDGET_CALLS": "20",
            "TASK_BUDGET_USD": "2.0",
            "LOG_LEVEL": "INFO",
        }
    )
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("访问了真实网络")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "request", boom)
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)


def _open(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "db" / "jobbot.sqlite")
    migrate(connection)
    return connection


def _add_job(
    connection: sqlite3.Connection,
    *,
    title: str,
    company: str,
    digest: str,
) -> tuple[str, str]:
    job_id = insert(connection, "job", {"platform": "manual", "external_id": None})
    snapshot_id = insert(
        connection,
        "job_snapshot",
        {
            "job_id": job_id,
            "fetched_at": "2026-11-04T00:00:00+00:00",
            "content_hash": digest,
            "title": title,
            "company": company,
            "city": "上海",
            "description": "岗位职责：维护数据平台。",
            "availability": "unknown",
            "source": "manual",
        },
    )
    return job_id, snapshot_id


def _add_material(connection: sqlite3.Connection, snapshot_id: str, *, revision: int) -> str:
    profile_id = insert(connection, "profile", {"name": "甲", "current_revision": 1})
    profile_snapshot_id = insert(
        connection,
        "profile_snapshot",
        {
            "profile_id": profile_id,
            "revision": 1,
            "fact_ids": [],
            "sha256": f"profile-{snapshot_id}-{revision}",
        },
    )
    return insert(
        connection,
        "material",
        {
            "profile_snapshot_id": profile_snapshot_id,
            "snapshot_id": snapshot_id,
            "kind": "resume",
            "mode": "pipeline",
            "revision": revision,
            "content": f"第 {revision} 版简历",
            "status": "approved",
            "content_hash": f"material-{snapshot_id}-{revision}",
        },
    )


def _events(connection: sqlite3.Connection, application_id: str) -> list[dict[str, object]]:
    rows = [row for row in list_rows(connection, "application_event") if row["application_id"] == application_id]
    rows.sort(key=lambda row: (str(row["at"]), str(row["created_at"]), str(row["id"])))
    return rows


def test_stage_correction_close_and_reopen_keep_one_application(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    job_id, snapshot_id = _add_job(connection, title="数据工程师", company="星河科技", digest="job-a")
    steps = (
        ("interested", "先关注", None),
        ("preparing", "纠正为准备材料", None),
        ("closed", "这个岗位先停", "地点不合适"),
        ("interviewing", "重新打开并进入面试", None),
    )
    application_id = ""
    for to_stage, reason, closed_reason in steps:
        updated = record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage=to_stage,
            reason=reason,
            closed_reason=closed_reason,
            material_id=None,
            note=None,
        )
        application_id = updated.application.id
        assert updated.event_written is True
        assert updated.application.stage == to_stage
        assert updated.application.job_id == job_id
        if to_stage == "closed":
            assert updated.application.closed_reason == "地点不合适"

    applications = [row for row in list_rows(connection, "application") if row["job_id"] == job_id]
    assert len(applications) == 1
    assert applications[0]["id"] == application_id
    assert applications[0]["stage"] == "interviewing"
    events = _events(connection, application_id)
    assert [(event["from_stage"], event["to_stage"], event["reason"], event["origin"]) for event in events] == [
        (None, "interested", "先关注", "manual"),
        ("interested", "preparing", "纠正为准备材料", "manual"),
        ("preparing", "closed", "这个岗位先停", "manual"),
        ("closed", "interviewing", "重新打开并进入面试", "manual"),
    ]
    reopened = list_events(connection, application_id)
    assert [event.origin for event in reopened] == ["manual", "manual", "manual", "manual"]
    assert reopened[2].from_stage == "preparing"
    assert reopened[2].to_stage == "closed"
    assert reopened[2].reason == "这个岗位先停"
    connection.close()


def test_applied_stays_put_and_sends_nothing(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    job_id, snapshot_id = _add_job(connection, title="后端工程师", company="北海", digest="job-b")
    material_id = _add_material(connection, snapshot_id, revision=2)
    updated = record_manual_stage(
        connection,
        job_id=job_id,
        snapshot_id=snapshot_id,
        to_stage="applied",
        reason="我已经在招聘平台自己提交",
        closed_reason=None,
        material_id=material_id,
        note="只是声明",
    )
    assert updated.application.stage == "applied"
    assert updated.application.material_id == material_id
    assert updated.event_written is True
    again = record_manual_stage(
        connection,
        job_id=job_id,
        snapshot_id=snapshot_id,
        to_stage="applied",
        reason="",
        closed_reason=None,
        material_id=material_id,
        note="只是声明",
    )
    assert again.application.id == updated.application.id
    assert again.application.stage == "applied"
    assert again.event_written is False
    assert len(list_rows(connection, "application")) == 1
    assert len(list_rows(connection, "application_event")) == 1
    assert list_rows(connection, "llm_call") == []
    assert list_rows(connection, "collect_command") == []
    event = _events(connection, updated.application.id)[0]
    assert event["origin"] == "manual"
    assert event["from_stage"] is None
    assert event["to_stage"] == "applied"
    for path in TRACKING_SOURCES:
        source = path.read_text(encoding="utf-8")
        for marker in OUTBOUND_MARKERS:
            assert marker not in source, (path.name, marker)
    view = (ROOT / "app" / "views" / "tracking.py").read_text(encoding="utf-8")
    assert "系统不核验" in view
    assert "不向任何外部地址发出请求" in view
    assert "不会自动推进阶段" in view
    assert "来源：手动" in view
    assert "没有代为投递" in view
    connection.close()


def test_stage_jump_does_not_fill_intermediate_stages(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    job_id, snapshot_id = _add_job(connection, title="分析师", company="南风", digest="job-c")
    updated = record_manual_stage(
        connection,
        job_id=job_id,
        snapshot_id=snapshot_id,
        to_stage="interested",
        reason="先记下",
        closed_reason=None,
        material_id=None,
        note=None,
    )
    jumped = record_manual_stage(
        connection,
        job_id=job_id,
        snapshot_id=snapshot_id,
        to_stage="offer",
        reason="直接改到 Offer",
        closed_reason=None,
        material_id=None,
        note=None,
    )
    assert jumped.application.id == updated.application.id
    assert jumped.application.stage == "offer"
    events = _events(connection, jumped.application.id)
    assert [event["to_stage"] for event in events] == ["interested", "offer"]
    assert events[1]["from_stage"] == "interested"
    assert "preparing" not in {event["to_stage"] for event in events}
    assert "applied" not in {event["to_stage"] for event in events}
    connection.close()


def test_rejected_update_writes_nothing(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    job_id, snapshot_id = _add_job(connection, title="工程师", company="东山", digest="job-d")
    other_job, other_snapshot = _add_job(connection, title="另一岗", company="东山", digest="job-e")
    other_material = _add_material(connection, other_snapshot, revision=1)
    with pytest.raises(JobBotError, match="需要填写原因"):
        record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage="ready",
            reason="  ",
            closed_reason=None,
            material_id=None,
            note=None,
        )
    with pytest.raises(JobBotError, match="关闭原因"):
        record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage="closed",
            reason="想关闭",
            closed_reason="",
            material_id=None,
            note=None,
        )
    with pytest.raises(JobBotError, match="阶段不在允许的枚举内"):
        record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage="submitted",
            reason="非法阶段",
            closed_reason=None,
            material_id=None,
            note=None,
        )
    with pytest.raises(JobBotError, match="材料不属于这个岗位"):
        record_manual_stage(
            connection,
            job_id=job_id,
            snapshot_id=snapshot_id,
            to_stage="applied",
            reason="错绑材料",
            closed_reason=None,
            material_id=other_material,
            note=None,
        )
    assert list_rows(connection, "application") == []
    assert list_rows(connection, "application_event") == []
    assert other_job
    connection.close()


def test_save_manual_stage_uses_configured_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    connection = _open(tmp_path)
    job_id, snapshot_id = _add_job(connection, title="产品", company="西岭", digest="job-f")
    connection.close()
    saved = save_manual_stage(
        job_id=job_id,
        snapshot_id=snapshot_id,
        to_stage="ready",
        reason="材料齐了，等我自己投",
        closed_reason=None,
        material_id=None,
        note=None,
    )
    check = _open(tmp_path)
    rows = list_rows(check, "application")
    assert len(rows) == 1
    assert rows[0]["id"] == saved.application.id
    assert rows[0]["stage"] == "ready"
    assert _events(check, saved.application.id)[0]["origin"] == "manual"
    check.close()


def test_event_origin_must_be_manual() -> None:
    with pytest.raises(ValueError, match="manual"):
        ApplicationEventRecord(
            id="evt",
            application_id="app",
            from_stage="interested",
            to_stage="applied",
            reason="自动",
            origin="auto",
            at="2026-11-04T00:00:00+00:00",
            created_at="2026-11-04T00:00:00+00:00",
            updated_at="2026-11-04T00:00:00+00:00",
        )


def test_page_keeps_manual_chinese_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    connection = _open(tmp_path)
    job_id, _snapshot_id = _add_job(connection, title="数据工程师", company="星河科技", digest="job-ui")
    connection.close()

    empty = AppTest.from_file(str(ROOT / "app" / "views" / "tracking.py"))
    # 空库用例单独跑在另一个目录，避免和下面的岗位共用 DATA_DIR。
    empty_dir = tmp_path / "empty"
    _prepare_env(monkeypatch, empty_dir)
    empty.run()
    assert not empty.exception, empty.exception
    empty_text = "\n".join(str(item.value) for item in (*empty.warning, *empty.info, *empty.caption))
    assert "系统不核验" in empty_text
    assert "不会自动推进阶段" in empty_text
    assert "还没有岗位" in empty_text
    assert "尚未实现" not in empty_text

    _prepare_env(monkeypatch, tmp_path)
    page = AppTest.from_file(str(ROOT / "app" / "views" / "tracking.py"))
    page.run()
    assert not page.exception, page.exception
    assert page.selectbox[0].value == job_id
    page.selectbox[1].set_value("applied")
    page.text_input[0].set_value("我已经在招聘平台自己提交")
    page.button[0].click().run()
    assert not page.exception, page.exception
    visible = "\n".join(
        str(item.value)
        for item in (*page.warning, *page.info, *page.caption, *page.text, *page.header, *page.subheader)
    )
    assert "已声明投递" in visible
    assert "没有联系外部平台" in visible
    assert "没有代为投递" in visible
    assert "来源：手动" in visible
    assert "阶段全部由你手动修改" in visible
    assert STAGE_LABEL["applied"] == "已声明投递"
    assert set(STAGE_LABEL) == set(STAGE_ORDER)

    check = _open(tmp_path)
    applications = list_rows(check, "application")
    assert len(applications) == 1
    assert applications[0]["stage"] == "applied"
    assert applications[0]["job_id"] == job_id
    assert _events(check, str(applications[0]["id"]))[0]["origin"] == "manual"
    check.close()
