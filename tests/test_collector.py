"""T04–T07。全部使用合成 fixture，不打开浏览器。"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from jobbot.config import ENV_KEYS
from jobbot.contracts import CollectLimits, ParseError
from jobbot.db.connection import connect, migrate
from jobbot.db.repository import get_by_id, list_rows

from jobbot.collector.detail_parser import parse_detail
from jobbot.collector.manual_import import import_manual
from jobbot.collector.runs import start_run, stop_run
from jobbot.collector.runner import process_detail
from jobbot.collector.stop_conditions import page_requests_stop
from jobbot.collector.store import save_draft

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "boss_detail"
COLLECTOR = ROOT / "src" / "jobbot" / "collector"
FORBIDDEN = ("startchat", "send", "upload", "submit")


@pytest.fixture
def connection(tmp_path):
    db = connect(tmp_path / "jobbot.sqlite")
    migrate(db)
    return db


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(name: str) -> dict:
    return _load(name)["body"]


def _run(connection, *, max_jobs: int = 30, max_minutes: int = 20, mode: str = "assist") -> str:
    return start_run(
        mode,
        "合成关键词",
        CollectLimits(max_jobs=max_jobs, max_minutes=max_minutes),
        connection=connection,
    )


def _feed(connection, run_id: str, name: str | None, *, http_status: int | None, raw_dir: Path, **kwargs):
    body = None if name is None else _body(name)
    if name is not None and http_status is None:
        http_status = _load(name)["http_status"]
    return process_detail(
        connection,
        run_id,
        http_status=http_status,
        body=body,
        verification_page=kwargs.get("verification_page", False),
        user_stop=kwargs.get("user_stop", False),
        elapsed_seconds=kwargs.get("elapsed_seconds", 0),
        raw_directory=raw_dir,
    )


def test_parse_complete_keeps_salary_text_and_drops_recruiter_identity() -> None:
    draft = parse_detail(_load("complete.json"))
    assert draft.title == "合成岗位-假数据-001"
    assert draft.company == "合成公司-900001"
    assert draft.city == "合成市-900001"
    assert draft.district is None
    assert draft.salary_text == "900-901K"
    assert draft.salary_min is None
    assert draft.salary_max is None
    assert draft.salary_unit is None
    assert draft.salary_months is None
    assert draft.experience_text == "99年"
    assert draft.degree_text == "合成学历-900001"
    assert draft.description == "合成描述，假编号 900001。这不是真实职位。"
    assert draft.external_id == "synthetic-job-900001"
    assert draft.hr_title == "合成称谓"
    assert draft.hr_active_text == "合成活跃描述"
    assert draft.platform == "boss"
    assert draft.source == "boss"
    dumped = draft.model_dump_json()
    assert "合成姓名甲" not in dumped
    assert "synthetic-user-900001" not in dumped


def test_parse_html_entities_and_missing_optional_fields() -> None:
    decoded = parse_detail(_load("html_entities.json"))
    assert decoded.title == "合成岗位 & 假数据-900006"
    assert decoded.salary_text == "10-20K&13薪"
    assert decoded.city == "合成市-900006"
    assert decoded.company == "合成公司&900006"
    assert decoded.degree_text == "本科&以上"
    assert decoded.hr_title == "合成称谓&顾问"
    assert "A&B" in decoded.description
    assert decoded.salary_min is None

    sparse = parse_detail(_load("missing_optional.json"))
    assert sparse.title == "合成岗位-假数据-900008"
    assert sparse.description.startswith("合成描述，假编号 900008")
    assert sparse.company is None
    assert sparse.city is None
    assert sparse.salary_text is None
    assert sparse.experience_text is None
    assert sparse.degree_text is None
    assert sparse.hr_title is None
    assert sparse.hr_active_text is None
    assert sparse.district is None
    assert sparse.url is None


def test_missing_required_fields_raise_and_do_not_store(connection, tmp_path) -> None:
    before = len(list_rows(connection, "job_snapshot"))
    for name in (
        "missing_job_name.json",
        "missing_post_description.json",
        "empty_post_description.json",
        "zpdata_empty.json",
    ):
        with pytest.raises(ParseError) as caught:
            parse_detail(_load(name))
        assert "没有" in str(caught.value)
        assert caught.value.saved is False
    unreadable = parse_detail(_load("unreadable_salary.json"))
    assert "\ue001" in (unreadable.salary_text or "")
    assert unreadable.salary_min is None
    assert len(list_rows(connection, "job_snapshot")) == before
    raw_dir = tmp_path / "raw"
    run_id = _run(connection)
    failed = _feed(connection, run_id, "missing_job_name.json", http_status=200, raw_dir=raw_dir)
    assert failed.stored is False
    assert failed.parse_failed is True
    assert failed.stop_reason is None
    assert failed.attempt_again is False
    assert len(list_rows(connection, "job_snapshot")) == before


def test_same_job_reuses_row_and_identical_content_does_not_add_snapshot(connection) -> None:
    first = parse_detail(_load("complete.json"))
    job_a, snap_a, created_a = save_draft(connection, first)
    second = first.model_copy(update={"description": "合成描述已变化，假编号 900001。这不是真实职位。"})
    job_b, snap_b, created_b = save_draft(connection, second)
    job_c, snap_c, created_c = save_draft(connection, second)
    assert created_a is True
    assert created_b is True
    assert created_c is False
    assert job_a.id == job_b.id == job_c.id
    assert snap_a.id != snap_b.id
    assert snap_b.id == snap_c.id
    assert len(list_rows(connection, "job")) == 1
    assert len(list_rows(connection, "job_snapshot")) == 2
    assert get_by_id(connection, "job", job_a.id)["latest_snapshot_id"] == snap_b.id
    salary_only = second.model_copy(update={"salary_text": "1-2K", "title": "只改标题"})
    job_d, snap_d, created_d = save_draft(connection, salary_only)
    assert created_d is False
    assert job_d.id == job_a.id
    assert snap_d.id == snap_b.id
    assert len(list_rows(connection, "job_snapshot")) == 2


def test_manual_text_and_json_import_and_empty_input(connection) -> None:
    with pytest.raises(ParseError) as empty:
        import_manual(connection=connection)
    assert "没有可导入的内容" in str(empty.value)
    with pytest.raises(ParseError):
        import_manual(text="   ", connection=connection)
    with pytest.raises(ParseError):
        import_manual(json_obj={}, connection=connection)
    pasted = import_manual(text="合成粘贴 JD &amp; 说明。这不是真实职位。", connection=connection)
    assert pasted.platform == "manual"
    assert pasted.source == "manual"
    assert pasted.title is None
    assert pasted.description == "合成粘贴 JD & 说明。这不是真实职位。"
    assert pasted.salary_min is None
    again = import_manual(text="合成粘贴 JD &amp; 说明。这不是真实职位。", connection=connection)
    assert again.id == pasted.id
    imported = import_manual(json_obj=_load("complete.json"), connection=connection)
    assert imported.platform == "manual"
    assert imported.source == "manual"
    assert imported.external_id == "synthetic-job-900001"
    assert imported.title == "合成岗位-假数据-001"
    flat = import_manual(
        json_obj={"title": "合成扁平岗位", "description": "合成扁平描述。这不是真实职位。", "salary_text": "面议"},
        connection=connection,
    )
    assert flat.platform == "manual"
    assert flat.title == "合成扁平岗位"
    assert flat.salary_text == "面议"
    assert flat.salary_min is None
    with pytest.raises(ParseError):
        import_manual(json_obj=_load("business_failure.json"), connection=connection)
    assert len(list_rows(connection, "job_snapshot")) == 3


def test_stop_conditions_keep_saved_rows_and_do_not_repeat(tmp_path) -> None:
    def open_case(name: str):
        db = connect(tmp_path / f"{name}.sqlite")
        migrate(db)
        return db

    raw_dir = tmp_path / "raw"
    connection = open_case("http")
    run_id = _run(connection, max_jobs=5, max_minutes=20)
    saved = _feed(connection, run_id, "complete.json", http_status=200, raw_dir=raw_dir)
    assert saved.stored is True
    assert saved.stop_reason is None
    assert len(list_rows(connection, "job_snapshot")) == 1

    http_stop = _feed(connection, run_id, "business_failure.json", http_status=503, raw_dir=raw_dir)
    assert http_stop.stop_reason == "http_not_200"
    assert http_stop.stored is False
    assert http_stop.attempt_again is False
    repeated = _feed(connection, run_id, "business_failure.json", http_status=503, raw_dir=raw_dir)
    assert repeated.n_seen == http_stop.n_seen
    assert repeated.stored is False
    assert len(list_rows(connection, "job_snapshot")) == 1

    connection = open_case("code")
    code_run = _run(connection)
    _feed(connection, code_run, "complete.json", http_status=200, raw_dir=raw_dir)
    code_stop = _feed(connection, code_run, "business_code.json", http_status=200, raw_dir=raw_dir)
    assert code_stop.stop_reason == "business_code_not_zero"
    assert code_stop.attempt_again is False
    assert len([row for row in list_rows(connection, "job_snapshot") if row["run_id"] == code_run]) == 1

    connection = open_case("empty")
    empty_run = _run(connection)
    _feed(connection, empty_run, "complete.json", http_status=200, raw_dir=raw_dir)
    empty_stop = _feed(connection, empty_run, "zpdata_empty.json", http_status=200, raw_dir=raw_dir)
    assert empty_stop.stop_reason == "zpdata_empty"
    assert empty_stop.attempt_again is False
    assert len(list_rows(connection, "job_snapshot")) == 1

    connection = open_case("verify")
    verify_run = _run(connection)
    _feed(connection, verify_run, "complete.json", http_status=200, raw_dir=raw_dir)
    verify_stop = _feed(
        connection,
        verify_run,
        "complete.json",
        http_status=200,
        raw_dir=raw_dir,
        verification_page=True,
    )
    assert verify_stop.stop_reason == "verification_page"
    assert verify_stop.stored is False
    assert verify_stop.n_ok == 1
    assert len([row for row in list_rows(connection, "job_snapshot") if row["run_id"] == verify_run]) == 1

    connection = open_case("user")
    user_run = _run(connection)
    _feed(connection, user_run, "complete.json", http_status=200, raw_dir=raw_dir)
    stop_run(user_run, connection=connection)
    user_stop = _feed(connection, user_run, "complete.json", http_status=200, raw_dir=raw_dir)
    assert user_stop.stop_reason == "user_stop"
    assert user_stop.n_ok == 1
    assert user_stop.attempt_again is False
    assert len(list_rows(connection, "job_snapshot")) == 1

    connection = open_case("parse")
    parse_run = _run(connection)
    _feed(connection, parse_run, "complete.json", http_status=200, raw_dir=raw_dir)
    first_fail = _feed(connection, parse_run, "missing_job_name.json", http_status=200, raw_dir=raw_dir)
    second_fail = _feed(connection, parse_run, "missing_post_description.json", http_status=200, raw_dir=raw_dir)
    assert first_fail.stop_reason is None
    assert second_fail.stop_reason is None
    third_fail = _feed(connection, parse_run, "empty_post_description.json", http_status=200, raw_dir=raw_dir)
    assert third_fail.stop_reason == "consecutive_parse_failures"
    assert third_fail.n_ok == 1
    assert third_fail.n_fail == 3
    assert third_fail.attempt_again is False
    stalled = _feed(connection, parse_run, "missing_job_name.json", http_status=200, raw_dir=raw_dir)
    assert stalled.n_seen == third_fail.n_seen
    assert len([row for row in list_rows(connection, "job_snapshot") if row["run_id"] == parse_run]) == 1

    connection = open_case("limit")
    limit_run = _run(connection, max_jobs=1)
    limited = _feed(connection, limit_run, "complete.json", http_status=200, raw_dir=raw_dir)
    assert limited.stored is True
    assert limited.stop_reason == "max_jobs"
    limited_again = _feed(connection, limit_run, "html_entities.json", http_status=200, raw_dir=raw_dir)
    assert limited_again.stored is False
    assert limited_again.n_ok == 1
    assert len([row for row in list_rows(connection, "job_snapshot") if row["run_id"] == limit_run]) == 1

    connection = open_case("time")
    timed_run = _run(connection, max_minutes=1)
    _feed(connection, timed_run, "complete.json", http_status=200, raw_dir=raw_dir, elapsed_seconds=0)
    timed = _feed(
        connection,
        timed_run,
        "html_entities.json",
        http_status=200,
        raw_dir=raw_dir,
        elapsed_seconds=60,
    )
    assert timed.stop_reason == "max_minutes"
    assert timed.stored is False
    assert timed.n_ok == 1
    assert timed.attempt_again is False
    assert len(list_rows(connection, "job_snapshot")) == 1


def test_raw_file_drops_recruiter_name(connection, tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    run_id = _run(connection)
    outcome = _feed(connection, run_id, "complete.json", http_status=200, raw_dir=raw_dir)
    assert outcome.raw_path is not None
    text = Path(outcome.raw_path).read_text(encoding="utf-8")
    assert "合成姓名甲" not in text
    assert "synthetic-user-900001" not in text
    assert "synthetic-job-900001" in text
    assert "合成岗位-假数据-001" in text


def test_login_page_flag_and_commands(connection) -> None:
    assert page_requests_stop("https://www.zhipin.com/web/user/login", "", jobs_already_seen=0) is False
    assert page_requests_stop("https://www.zhipin.com/web/user/login", "", jobs_already_seen=1) is True
    assert page_requests_stop("https://www.zhipin.com/verify", "", jobs_already_seen=0) is True
    assert page_requests_stop("https://www.zhipin.com/", "请完成验证", jobs_already_seen=0) is True
    run_id = _run(connection)
    row = get_by_id(connection, "collect_run", run_id)
    assert row["status"] == "running"
    assert row["mode"] == "assist"
    assert row["n_ok"] == 0
    stop_run(run_id, connection=connection)
    commands = [item for item in list_rows(connection, "collect_command") if item["run_id"] == run_id]
    assert [item["command"] for item in commands] == ["start", "stop"]


def test_auto_mode_does_not_open_browser(monkeypatch, connection) -> None:
    def _refuse(*_args, **_kwargs):
        raise AssertionError("自动点选模式不应监听或打开浏览器")

    monkeypatch.setattr("jobbot.collector.runner._listen", _refuse)
    run_id = _run(connection, mode="auto")
    from jobbot.collector.runner import run_assist

    assert run_assist(run_id, connection=connection) == run_id
    row = get_by_id(connection, "collect_run", run_id)
    assert row["stop_reason"] == "auto_mode_not_implemented"
    assert row["status"] == "stopped"
    assert "playwright" not in sys.modules


def test_missing_browser_library_is_explicit(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("synthetic missing library")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    from jobbot.collector.runner import _load_playwright
    from jobbot.contracts import CollectorBlocked

    with pytest.raises(CollectorBlocked) as caught:
        _load_playwright()
    assert "Playwright" in str(caught.value)
    assert caught.value.stop_reason == "playwright_missing"


def test_runner_imports_browser_library_only_inside_function() -> None:
    source = (COLLECTOR / "runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert all(not name.startswith("playwright") for name in names)
    assert "launch_persistent_context" in source
    assert "headless=False" in source
    assert "user_data_dir" in source
    assert "browser-profile" in source
    assert "/wapi/zpgeek/job/detail.json" in source
    sys.modules.pop("playwright", None)
    sys.modules.pop("playwright.sync_api", None)
    import importlib

    import jobbot.collector.runner as runner

    importlib.reload(runner)
    assert "playwright" not in sys.modules


def test_collector_source_has_no_write_actions() -> None:
    offenders: list[str] = []
    for path in sorted(COLLECTOR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for word in FORBIDDEN:
            if word in text:
                offenders.append(f"{path.name}: {word}")
        if ".click(" in text or ".fill(" in text:
            offenders.append(f"{path.name}: pointer action")
    assert offenders == []


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


def _visible(app: object) -> str:
    chunks: list[str] = []
    for element_name in ("title", "header", "subheader", "markdown", "warning", "error", "info", "caption", "text"):
        for element in getattr(app, element_name):
            chunks.append(str(element.value))
    for button in getattr(app, "button"):
        chunks.append(str(button.label))
    return "\n".join(chunks)


def test_collect_page_idle_has_no_success_and_import_works(monkeypatch, tmp_path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "collect.py"))
    app.run()
    assert not app.exception, app.exception
    idle = _visible(app)
    assert "启动采集" in idle
    assert "停止采集" in idle
    assert "粘贴导入" in idle
    assert "成功" not in idle
    assert "数据未保存" in idle

    import_button = next(button for button in app.button if button.label == "导入")
    import_button.click().run()
    rejected = _visible(app)
    assert "没有可导入的内容" in rejected or "数据未保存" in rejected
    assert "成功" not in rejected

    app.text_area[0].set_value("合成页面导入。这不是真实职位。").run()
    import_button = next(button for button in app.button if button.label == "导入")
    import_button.click().run()
    saved = _visible(app)
    assert "已保存 1 条快照" in saved
    assert "手动导入" in saved
    assert "成功" not in saved
    database = tmp_path / "db" / "jobbot.sqlite"
    assert database.is_file()
