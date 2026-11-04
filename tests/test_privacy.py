import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from jobbot.config import ENV_KEYS, Settings
from jobbot.contracts import JobBotError
from jobbot.llm.cache import make_cache_key
from jobbot.llm.gateway import Gateway, HttpResponse
from jobbot.llm.redact import (
    ADDRESS_PLACEHOLDER,
    EMAIL_PLACEHOLDER,
    NAME_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    redact_text,
    redact_value,
)
from jobbot.llm.schemas import schema_version_of
from jobbot.privacy import (
    clear_user_data,
    confirm_cloud_outbound,
    load_privacy_preferences,
    privacy_path,
    set_redact_before_send,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_JD = "\n".join(
    [
        "岗位职责：负责接口开发，联系人电话13800000000，邮箱 candidate@example.com。",
        "姓名：测甲",
        "住址：测试省测试市测试区虚构路8号",
        "任职要求：熟悉 Python。",
    ]
)


class Echo(BaseModel):
    value: str


class ScriptedTransport:
    def __init__(self, steps: list[object]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, object]] = []

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        action = self.steps.pop(0)
        assert isinstance(action, HttpResponse)
        return action


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        llm_base_url="http://llm.test/v1",
        llm_api_key="local-test-key",
        llm_model="test-model",
        llm_mode="live",
        embedding_model="BAAI/bge-small-zh-v1.5",
        embedding_revision="",
        collect_max_jobs=30,
        collect_max_minutes=20,
        task_budget_calls=20,
        task_budget_usd=2.0,
        log_level="INFO",
    )


def _ok() -> HttpResponse:
    body = {"choices": [{"message": {"content": json.dumps({"value": "ok"})}}], "usage": {}}
    return HttpResponse(200, body)


def _user_message(payload: object) -> str:
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    content = messages[1]["content"]
    assert isinstance(content, str)
    return content


def test_redact_replaces_contacts_and_keeps_job_text() -> None:
    redacted = redact_text(SAMPLE_JD)
    assert "负责接口开发" in redacted
    assert "熟悉 Python" in redacted
    assert "测甲" not in redacted
    assert "13800000000" not in redacted
    assert "candidate@example.com" not in redacted
    assert "虚构路" not in redacted
    assert PHONE_PLACEHOLDER in redacted
    assert EMAIL_PLACEHOLDER in redacted
    assert NAME_PLACEHOLDER in redacted
    assert ADDRESS_PLACEHOLDER in redacted
    assert redact_text(redacted) == redacted


def test_redact_phone_email_and_bare_address_forms() -> None:
    text = "\n".join(
        [
            "请拨打 +86-138-0000-0000 或 １３８０００００００１。",
            "来信 candidate+lab@example.com。",
            "公司在测试市测试区虚构路8号办公。",
            "岗位职责：编写接口并维护服务。",
        ]
    )
    redacted = redact_text(text)
    assert "138-0000-0000" not in redacted
    assert "１３８０００００００１" not in redacted
    assert "candidate+lab@example.com" not in redacted
    assert "虚构路8号" not in redacted
    assert "编写接口并维护服务" in redacted
    assert "办公" in redacted
    untouched = "岗位职责：参与后端服务开发，和产品一起拆需求。"
    assert redact_text(untouched) == untouched


def test_redact_value_keeps_structure() -> None:
    value = {"facts": ["姓名：测乙"], "count": 2, "note": None}
    redacted = redact_value(value)
    assert redacted["count"] == 2
    assert redacted["note"] is None
    assert redacted["facts"] == [NAME_PLACEHOLDER]
    assert "测乙" not in str(redacted)


def test_unconfirmed_gateway_does_not_post(tmp_path: Path) -> None:
    transport = ScriptedTransport([_ok()])
    gateway = Gateway(_settings(tmp_path), transport, sleep=lambda _seconds: None)
    with pytest.raises(JobBotError, match="没有发出真实请求") as caught:
        gateway.call("extract", "模板甲", {"jd": SAMPLE_JD}, Echo)
    assert caught.value.saved is False
    assert "简历事实" in str(caught.value)
    assert "JD 文本" in str(caught.value)
    assert transport.calls == []
    assert list(tmp_path.joinpath("cache").glob("**/*")) == []


def test_redact_switch_changes_outbound_but_not_cache_key(tmp_path: Path) -> None:
    variables = {"jd": SAMPLE_JD, "facts": {"line": "姓名：测甲"}}
    template = "联系 candidate@example.com"
    confirm_cloud_outbound(tmp_path)
    set_redact_before_send(tmp_path, False)
    plain_transport = ScriptedTransport([_ok()])
    plain = Gateway(_settings(tmp_path), plain_transport, sleep=lambda _seconds: None)
    plain.call("extract", template, variables, Echo)
    plain_body = _user_message(plain_transport.calls[0]["payload"])
    assert "13800000000" in plain_body
    assert "测甲" in plain_body

    set_redact_before_send(tmp_path, True)
    redacted_variables = redact_value(variables)
    assert isinstance(redacted_variables, dict)
    original_key = make_cache_key(
        model_id="test-model",
        template=template,
        variables=variables,
        temperature=0,
        schema_version=schema_version_of(Echo),
    )
    changed_key = make_cache_key(
        model_id="test-model",
        template=redact_text(template),
        variables=redacted_variables,
        temperature=0,
        schema_version=schema_version_of(Echo),
    )
    assert original_key != changed_key
    transport = ScriptedTransport([_ok(), _ok()])
    gateway = Gateway(_settings(tmp_path), transport, sleep=lambda _seconds: None)
    other_variables = {"jd": SAMPLE_JD + "\n补充：负责值班。", "facts": {"line": "姓名：测甲"}}
    gateway.call("extract", template, other_variables, Echo)
    body = _user_message(transport.calls[0]["payload"])
    assert "13800000000" not in body
    assert "candidate@example.com" not in body
    assert "测甲" not in body
    assert "虚构路" not in body
    assert "负责接口开发" in body
    assert "负责值班" in body
    assert PHONE_PLACEHOLDER in body
    stored_key = make_cache_key(
        model_id="test-model",
        template=template,
        variables=other_variables,
        temperature=0,
        schema_version=schema_version_of(Echo),
    )
    assert (tmp_path / "cache" / "llm" / f"{stored_key}.json").is_file()
    gateway.call("extract", template, other_variables, Echo)
    assert len(transport.calls) == 1


def test_clear_user_data_empties_targets_and_reports_in_chinese(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    for relative in (
        "db/jobbot.sqlite",
        "raw/resumes/a.txt",
        "cache/llm/item.json",
        "embeddings/rev/vec.json",
        "vectors/extra.json",
        "exports/out.txt",
        "models/keep.bin",
        "browser-profile/state.txt",
    ):
        path = data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload", encoding="utf-8")
    (data_dir / "keep.txt").write_text("root-keep", encoding="utf-8")
    (data_dir / "raw" / "link").symlink_to(secret)
    confirm_cloud_outbound(data_dir)
    set_redact_before_send(data_dir, True)

    result = clear_user_data(data_dir)
    text = result.text()
    assert result.ok
    assert "核对通过" in text
    assert "已清空" in text
    assert "没有删除仓库源码" in text
    for name in ("db", "raw", "cache", "embeddings", "vectors", "exports"):
        leftover = [path for path in (data_dir / name).rglob("*") if path.is_file() or path.is_symlink()]
        assert leftover == []
    assert (data_dir / "models" / "keep.bin").read_text(encoding="utf-8") == "payload"
    assert (data_dir / "browser-profile" / "state.txt").read_text(encoding="utf-8") == "payload"
    assert (data_dir / "keep.txt").read_text(encoding="utf-8") == "root-keep"
    assert secret.read_text(encoding="utf-8") == "secret"
    assert load_privacy_preferences(data_dir).confirmed is True
    assert load_privacy_preferences(data_dir).redact_before_send is True
    assert not (data_dir / "db" / "jobbot.sqlite").exists()

    linked = tmp_path / "linked-data"
    (linked / "raw").mkdir(parents=True)
    (linked / "cache").symlink_to(outside, target_is_directory=True)
    link_result = clear_user_data(linked)
    assert link_result.ok
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (linked / "cache").is_dir()
    assert not (linked / "cache").is_symlink()


def test_clear_refuses_repository_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "jobbot" / "keep.py"
    source.parent.mkdir(parents=True)
    source.write_text("print(1)\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    database = repo / "db" / "jobbot.sqlite"
    database.parent.mkdir()
    database.write_text("db", encoding="utf-8")
    result = clear_user_data(repo)
    assert result.ok is False
    assert "拒绝清除" in result.text()
    assert "没有删除仓库源码" in result.text()
    assert database.read_text(encoding="utf-8") == "db"
    assert source.read_text(encoding="utf-8") == "print(1)\n"


def test_confirmation_file_is_not_a_database(tmp_path: Path) -> None:
    confirm_cloud_outbound(tmp_path)
    path = privacy_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["confirmed"] is True
    assert list(tmp_path.rglob("*.sqlite")) == []


def _prepare_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> None:
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
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _visible(app: object) -> str:
    chunks: list[str] = []
    for element_name in ("title", "header", "subheader", "markdown", "warning", "error", "info", "caption", "text"):
        for element in getattr(app, element_name):
            chunks.append(str(element.value))
    for checkbox in getattr(app, "checkbox"):
        chunks.append(str(checkbox.label))
    return "\n".join(chunks)


def test_settings_page_confirms_redacts_masks_key_and_clears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from streamlit.testing.v1 import AppTest

    secret = "local-" + ("k" * 16)
    _prepare_env(monkeypatch, tmp_path, LLM_API_KEY=secret)
    for relative in ("db/jobbot.sqlite", "raw/a.txt", "cache/a.json", "embeddings/a.json", "exports/a.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "keep.bin").write_text("model", encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "app" / "main.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "简历事实" in text
    assert "JD 文本" in text
    assert "发送前脱敏" in text
    assert "可能降低效果" in text
    assert "尚未确认" in text
    assert secret not in text
    assert secret[-4:] in text
    assert "local-test-key" not in text

    app.checkbox[0].set_value(True).run()
    assert load_privacy_preferences(tmp_path).redact_before_send is True
    assert secret not in privacy_path(tmp_path).read_text(encoding="utf-8")

    confirm = next(button for button in app.button if button.label == "确认发送简历事实与 JD 文本")
    confirm.click().run()
    assert load_privacy_preferences(tmp_path).confirmed is True
    assert "已确认" in _visible(app)
    assert "没有写入数据库" in _visible(app)

    clear = next(button for button in app.button if button.label == "清除数据")
    clear.click().run()
    assert not app.exception, app.exception
    cleared = _visible(app)
    assert "核对" in cleared
    assert "已清空" in cleared
    assert secret not in cleared
    for name in ("db", "raw", "cache", "embeddings", "exports"):
        leftover = [path for path in (tmp_path / name).rglob("*") if path.is_file() or path.is_symlink()]
        assert leftover == []
    assert (tmp_path / "models" / "keep.bin").read_text(encoding="utf-8") == "model"
