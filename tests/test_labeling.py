import re
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from jobbot.config import ENV_KEYS
from jobbot.contracts import JobBotError
from jobbot.db.connection import connect, migrate
from jobbot.db.repository import insert, list_rows
from jobbot.labeling import quadratic_weighted_kappa, save_label

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "views" / "labels.py"
QUESTIONNAIRE = ROOT / "eval" / "survey" / "questionnaire.md"
DATASET_README = ROOT / "eval" / "datasets" / "README.md"

_JD_TEXT = "合成岗位只写职责：整理测试记录。"
_CONFIRMED_FACT = "已确认的合成经历：写过课程作业。"
_HIDDEN_FACT = "未确认的句子不要出现。"
_SCORE_TEXT = "0.87321"
_RANK_TEXT = "17"
_METHOD = "b2"
_FORBIDDEN_UI = ("模型分数", "排名", "方法名")
_METHOD_NAME = re.compile(r"\b(?:b0|b1|b2|b3|llm)\b")


def _prepare_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = {key: "" for key in ENV_KEYS}
    values.update(
        {
            "DATA_DIR": str(tmp_path),
            "LLM_MODE": "live",
            "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5",
            "COLLECT_MAX_JOBS": "30",
            "COLLECT_MAX_MINUTES": "20",
            "TASK_BUDGET_CALLS": "200",
            "TASK_BUDGET_USD": "2.0",
            "LOG_LEVEL": "INFO",
        }
    )
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "db" / "jobbot.sqlite")
    migrate(connection)
    return connection


def _seed_blind_page(connection: sqlite3.Connection) -> tuple[str, str]:
    profile_id = insert(connection, "profile", {"name": "合成档案", "current_revision": 1})
    insert(
        connection,
        "fact",
        {
            "profile_id": profile_id,
            "fact_key": "work-confirmed",
            "revision": 1,
            "category": "work",
            "statement": _CONFIRMED_FACT,
            "status": "confirmed",
        },
    )
    insert(
        connection,
        "fact",
        {
            "profile_id": profile_id,
            "fact_key": "work-candidate",
            "revision": 1,
            "category": "work",
            "statement": _HIDDEN_FACT,
            "status": "candidate",
        },
    )
    job_id = insert(connection, "job", {"platform": "manual", "external_id": None})
    snapshot_id = insert(
        connection,
        "job_snapshot",
        {
            "job_id": job_id,
            "fetched_at": "2026-11-04T00:00:00+00:00",
            "content_hash": "synthetic-jd",
            "description": _JD_TEXT,
            "availability": "unknown",
            "source": "manual",
        },
    )
    profile_snapshot_id = insert(
        connection,
        "profile_snapshot",
        {
            "profile_id": profile_id,
            "revision": 1,
            "fact_ids": [],
            "sha256": "synthetic-profile",
        },
    )
    preference_id = insert(
        connection,
        "preference",
        {
            "profile_id": profile_id,
            "revision": 1,
            "hard": [{"field": "city", "operator": "eq", "value": "杭州"}],
            "soft": [],
            "search_terms": [],
        },
    )
    run_id = insert(
        connection,
        "match_run",
        {
            "profile_snapshot_id": profile_snapshot_id,
            "preference_id": preference_id,
            "method": _METHOD,
            "config": {"method": _METHOD, "score": _SCORE_TEXT},
            "config_hash": "synthetic-config",
            "n_jobs": 1,
        },
    )
    insert(
        connection,
        "match_result",
        {
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "hard_result": "pass",
            "score": float(_SCORE_TEXT),
            "rank": int(_RANK_TEXT),
            "components": {"method": _METHOD, "score": _SCORE_TEXT, "rank": _RANK_TEXT},
            "evidence": f"method={_METHOD};score={_SCORE_TEXT};rank={_RANK_TEXT}",
        },
    )
    return profile_id, snapshot_id


def _visible(app: AppTest) -> str:
    chunks: list[str] = []
    for name in ("header", "subheader", "markdown", "warning", "error", "info", "caption", "text"):
        for element in getattr(app, name):
            chunks.append(str(element.value))
    for button in app.button:
        chunks.append(str(button.label))
    for field in app.text_input:
        chunks.append(str(field.label))
        chunks.append(str(field.value))
    for field in app.text_area:
        chunks.append(str(field.label))
        chunks.append(str(field.value))
    for field in app.selectbox:
        chunks.append(str(field.label))
        chunks.append(str(field.value))
        chunks.extend(str(option) for option in field.options)
    return "\n".join(chunks)


def _button(app: AppTest, label: str):
    for button in app.button:
        if button.label == label:
            return button
    raise AssertionError(label)


def _input(app: AppTest, label: str):
    for field in app.text_input:
        if field.label == label:
            return field
    raise AssertionError(label)


def test_same_profile_snapshot_labeler_and_round_cannot_insert_second_row(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    profile_id, snapshot_id = _seed_blind_page(connection)
    first_id = save_label(
        connection,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
        rating=2,
        hard_label="pass",
        labeler="标注甲",
        round="first",
        note="依据写在备注",
    )
    with pytest.raises(JobBotError, match="不能插入第二行"):
        save_label(
            connection,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
            rating=0,
            hard_label="fail",
            labeler="标注甲",
            round="first",
        )
    rows = list_rows(connection, "label")
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    assert rows[0]["rating"] == 2
    assert rows[0]["hard_label"] == "pass"
    recheck_id = save_label(
        connection,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
        rating=1,
        hard_label="unclear",
        labeler="标注甲",
        round="recheck",
    )
    second_labeler_id = save_label(
        connection,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
        rating=3,
        hard_label="pass",
        labeler="标注乙",
        round="first",
    )
    stored = {row["id"]: row for row in list_rows(connection, "label")}
    assert stored[recheck_id]["round"] == "recheck"
    assert stored[second_labeler_id]["labeler"] == "标注乙"
    assert len(stored) == 3
    connection.close()


def test_skip_does_not_store_zero_rating(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    connection = _connection(tmp_path)
    _seed_blind_page(connection)
    connection.close()

    app = AppTest.from_file(str(PAGE), default_timeout=20)
    app.run()
    assert not app.exception, app.exception
    _input(app, "标注者").set_value("标注甲")
    _button(app, "跳过").click()
    app.run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert "0 分" in visible
    assert "没有写入标注" in visible or "数据未保存" in visible

    connection = _connection(tmp_path)
    assert list_rows(connection, "label") == []
    saved_id = save_label(
        connection,
        profile_id=list_rows(connection, "profile")[0]["id"],
        snapshot_id=list_rows(connection, "job_snapshot")[0]["id"],
        rating=0,
        hard_label="fail",
        labeler="标注甲",
        round="first",
        note="这是不合适，不是跳过",
    )
    stored = list_rows(connection, "label")
    assert len(stored) == 1
    assert stored[0]["id"] == saved_id
    assert stored[0]["rating"] == 0
    assert stored[0]["note"] == "这是不合适，不是跳过"
    connection.close()


def test_blind_page_hides_model_score_rank_and_method(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_env(monkeypatch, tmp_path)
    connection = _connection(tmp_path)
    _seed_blind_page(connection)
    connection.close()

    app = AppTest.from_file(str(PAGE), default_timeout=20)
    app.run()
    assert not app.exception, app.exception
    visible = _visible(app)
    source = PAGE.read_text(encoding="utf-8")
    shown = visible + "\n" + source
    assert _JD_TEXT in visible
    assert _CONFIRMED_FACT in visible
    assert "杭州" in visible
    assert _HIDDEN_FACT not in visible
    assert _SCORE_TEXT not in shown
    assert _RANK_TEXT not in shown
    for phrase in _FORBIDDEN_UI:
        assert phrase not in shown
    assert _METHOD_NAME.search(shown) is None


def test_quadratic_weighted_kappa_uses_synthetic_sample_and_writes_no_report(tmp_path: Path) -> None:
    value = quadratic_weighted_kappa([0, 1, 2], [0, 1, 3])
    assert value == pytest.approx(6 / 7)
    assert quadratic_weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]) == 1.0
    assert quadratic_weighted_kappa([0], [3]) == 0.0
    with pytest.raises(JobBotError, match="跳过") as skipped:
        quadratic_weighted_kappa([1, None], [1, 0])
    message = skipped.value.user_text()
    assert "0" in message
    assert "没有写成一致性报告" in message
    with pytest.raises(JobBotError, match="没有写成一致性报告"):
        quadratic_weighted_kappa([True], [1])
    with pytest.raises(JobBotError, match="没有写成一致性报告"):
        quadratic_weighted_kappa([], [])
    agreement = (ROOT / "src" / "jobbot" / "labeling" / "agreement.py").read_text(encoding="utf-8")
    assert "open(" not in agreement
    assert "write_text" not in agreement
    assert "一致性为" not in agreement
    assert list(tmp_path.iterdir()) == []


def test_questionnaire_is_unpublished_and_has_no_responses() -> None:
    text = QUESTIONNAIRE.read_text(encoding="utf-8")
    assert "尚未发布" in text
    assert "没有回收" in text
    assert "没有作答" in text
    assert "没有汇总" in text
    assert "有效问卷" not in text
    assert "回收率" not in text


def test_dataset_readme_states_no_real_resume_or_jd() -> None:
    text = DATASET_README.read_text(encoding="utf-8")
    assert "没有同学简历" in text
    assert "没有真实 JD" in text
    assert "原简历不进仓库" in text
    assert "这个目录是空的" in text
