import sqlite3

import pytest

from jobbot.contracts import JobBotError
from jobbot.db.connection import connect, migrate
from jobbot.db.repository import get_by_id, insert, update_by_id

EXPECTED_TABLES = {
    "document",
    "profile",
    "fact",
    "profile_snapshot",
    "preference",
    "collect_run",
    "collect_command",
    "job",
    "job_snapshot",
    "jd_parse",
    "match_run",
    "match_result",
    "label",
    "feedback",
    "material",
    "claim",
    "question",
    "application",
    "application_event",
    "llm_call",
    "experiment_run",
}

REQUIRED_INDEXES = {
    "job": ["platform", "external_id"],
    "job_snapshot": ["job_id", "fetched_at"],
    "fact": ["profile_id", "status"],
    "match_result": ["run_id", "rank"],
    "label": ["profile_id"],
    "llm_call": ["purpose", "created_at"],
}


@pytest.fixture
def connection(tmp_path):
    db = connect(tmp_path / "jobbot.sqlite")
    migrate(db)
    return db


def _index_columns(connection: sqlite3.Connection, table: str) -> list[list[str]]:
    found: list[list[str]] = []
    for index_row in connection.execute(f"PRAGMA index_list('{table}')"):
        columns = [
            info[2]
            for info in connection.execute(f"PRAGMA index_info('{index_row[1]}')")
        ]
        found.append(columns)
    return found


def test_migration_is_repeatable_and_enables_fk_wal(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    names = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert EXPECTED_TABLES <= names
    profile_id = insert(connection, "profile", {"name": "甲", "current_revision": 1})
    migrate(connection)
    assert get_by_id(connection, "profile", profile_id)["name"] == "甲"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_required_indexes_and_foreign_keys_exist(connection: sqlite3.Connection) -> None:
    for table, expected in REQUIRED_INDEXES.items():
        columns = _index_columns(connection, table)
        assert any(cols[: len(expected)] == expected for cols in columns), (table, columns)
    for table in ("fact", "job_snapshot", "material", "application", "llm_call"):
        if table == "llm_call":
            continue
        foreign_keys = list(connection.execute(f"PRAGMA foreign_key_list('{table}')"))
        assert foreign_keys, table
    with pytest.raises(JobBotError, match="写入 fact 被拒绝"):
        insert(
            connection,
            "fact",
            {
                "profile_id": "missing-profile",
                "fact_key": "k",
                "revision": 1,
                "category": "skill",
                "statement": "会 Python",
                "status": "candidate",
            },
        )


def _seed_snapshot(connection: sqlite3.Connection) -> tuple[str, str]:
    job_id = insert(connection, "job", {"platform": "manual", "external_id": None})
    snapshot_id = insert(
        connection,
        "job_snapshot",
        {
            "job_id": job_id,
            "fetched_at": "2026-11-04T00:00:00+00:00",
            "content_hash": "abc",
            "description": "岗位职责：写 Python。",
            "availability": "unknown",
            "source": "manual",
        },
    )
    return job_id, snapshot_id


def test_job_snapshot_content_cannot_be_updated(connection: sqlite3.Connection) -> None:
    _job_id, snapshot_id = _seed_snapshot(connection)
    with pytest.raises(JobBotError, match="正文字段不可更新"):
        update_by_id(connection, "job_snapshot", snapshot_id, {"description": "被改掉的正文"})
    assert get_by_id(connection, "job_snapshot", snapshot_id)["description"] == "岗位职责：写 Python。"
    with pytest.raises(sqlite3.IntegrityError, match="正文字段不可更新"):
        connection.execute(
            "UPDATE job_snapshot SET title = ? WHERE id = ?",
            ("新标题", snapshot_id),
        )
    connection.execute(
        "UPDATE job_snapshot SET updated_at = ? WHERE id = ?",
        ("2026-11-04T01:00:00+00:00", snapshot_id),
    )
    connection.commit()
    assert get_by_id(connection, "job_snapshot", snapshot_id)["updated_at"] == "2026-11-04T01:00:00+00:00"
    assert get_by_id(connection, "job_snapshot", snapshot_id)["title"] is None


def test_approved_material_content_cannot_change(connection: sqlite3.Connection) -> None:
    profile_id = insert(connection, "profile", {"name": "甲", "current_revision": 1})
    profile_snapshot_id = insert(
        connection,
        "profile_snapshot",
        {"profile_id": profile_id, "revision": 1, "fact_ids": [], "sha256": "p"},
    )
    _job_id, snapshot_id = _seed_snapshot(connection)
    material_id = insert(
        connection,
        "material",
        {
            "profile_snapshot_id": profile_snapshot_id,
            "snapshot_id": snapshot_id,
            "kind": "resume",
            "mode": "pipeline",
            "revision": 1,
            "content": "参与接口开发",
            "status": "draft",
            "content_hash": "h1",
        },
    )
    update_by_id(connection, "material", material_id, {"content": "参与接口开发与测试", "content_hash": "h2"})
    assert "测试" in get_by_id(connection, "material", material_id)["content"]
    update_by_id(connection, "material", material_id, {"status": "approved"})
    with pytest.raises(JobBotError, match="approved"):
        update_by_id(connection, "material", material_id, {"content": "主导了全部架构"})
    assert get_by_id(connection, "material", material_id)["content"] == "参与接口开发与测试"
    with pytest.raises(sqlite3.IntegrityError, match="approved"):
        connection.execute(
            "UPDATE material SET content = ? WHERE id = ?",
            ("另一版正文", material_id),
        )
    update_by_id(connection, "material", material_id, {"status": "superseded"})
    assert get_by_id(connection, "material", material_id)["status"] == "superseded"
    assert get_by_id(connection, "material", material_id)["content"] == "参与接口开发与测试"
