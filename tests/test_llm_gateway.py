import json
import logging
import sqlite3
import time

import pytest
from pydantic import BaseModel

from jobbot.config import Settings
from jobbot.contracts import Budget, BudgetExceeded, JobBotError, LLMUnavailable
from jobbot.llm.gateway import REPLAY_CACHE_MISS, Gateway, HttpResponse


class Echo(BaseModel):
    value: str


class ScriptedTransport:
    def __init__(self, steps: list[object]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, object]] = []

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        action = self.steps.pop(0)
        if isinstance(action, Exception):
            raise action
        assert isinstance(action, HttpResponse)
        return action


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "llm_base_url": "http://llm.test/v1",
        "llm_api_key": "test-key",
        "llm_model": "test-model",
        "llm_mode": "live",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "embedding_revision": "",
        "collect_max_jobs": 30,
        "collect_max_minutes": 20,
        "task_budget_calls": 20,
        "task_budget_usd": 2.0,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)


def _ok(value: str = "ok", cost: float | None = None) -> HttpResponse:
    usage: dict[str, object] = {}
    if cost is not None:
        usage["cost"] = cost
    body = {"choices": [{"message": {"content": json.dumps({"value": value})}}], "usage": usage}
    return HttpResponse(200, body)


def _gateway(tmp_path, steps: list[object], **overrides: object) -> tuple[Gateway, ScriptedTransport, list[float]]:
    slept: list[float] = []
    transport = ScriptedTransport(steps)
    gateway = Gateway(_settings(tmp_path, **overrides), transport, sleep=slept.append)
    return gateway, transport, slept


def test_cache_hit_does_not_call_again(tmp_path) -> None:
    gateway, transport, _slept = _gateway(tmp_path, [_ok("一次")])
    variables = {"text": "同一输入"}
    first = gateway.call("extract", "模板甲", variables, Echo)
    second = gateway.call("extract", "模板甲", variables, Echo)
    assert len(transport.calls) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.parsed.value == "一次"
    assert transport.calls[0]["payload"]["temperature"] == 0  # type: ignore[index]


def test_budget_stops_before_http_and_after_priced_call(tmp_path) -> None:
    gateway, transport, _slept = _gateway(tmp_path, [_ok(cost=1.5)])
    with pytest.raises(BudgetExceeded, match="TASK_BUDGET_CALLS") as calls_error:
        gateway.call("extract", "模板甲", {"text": "a"}, Echo, Budget(max_calls=0, max_usd=2))
    assert calls_error.value.saved is False
    assert len(transport.calls) == 0

    with pytest.raises(BudgetExceeded, match="TASK_BUDGET_USD") as usd_error:
        gateway.call("extract", "模板甲", {"text": "b"}, Echo, Budget(max_calls=3, max_usd=1))
    assert "不会继续请求模型" in str(usd_error.value)
    assert len(transport.calls) == 1
    assert list(tmp_path.joinpath("cache/llm").glob("*.json")) == []


def test_server_error_retries_twice_with_backoff(tmp_path) -> None:
    failure = HttpResponse(503, {"error": {"message": "unavailable"}})
    gateway, transport, slept = _gateway(tmp_path, [failure, failure, failure, failure])
    started = time.monotonic()
    with pytest.raises(LLMUnavailable, match="503"):
        gateway.call("extract", "模板甲", {"text": "a"}, Echo)
    assert len(transport.calls) == 3
    assert slept == [2.0, 8.0]
    assert time.monotonic() - started < 1


def test_timeout_retries_twice_with_backoff_and_does_not_sleep_for_real(tmp_path) -> None:
    gateway, transport, slept = _gateway(
        tmp_path,
        [TimeoutError("timeout"), TimeoutError("timeout"), TimeoutError("timeout")],
    )
    started = time.monotonic()
    with pytest.raises(LLMUnavailable, match="已重试 2 次"):
        gateway.call("extract", "模板甲", {"text": "a"}, Echo)
    elapsed = time.monotonic() - started
    assert len(transport.calls) == 3
    assert slept == [2.0, 8.0]
    assert elapsed < 1


def test_429_uses_retry_after_and_stops_after_two_retries(tmp_path) -> None:
    limited = HttpResponse(429, {"error": {"message": "slow down"}}, {"Retry-After": "5"})
    gateway, transport, slept = _gateway(tmp_path, [limited, limited, _ok("过了")])
    started = time.monotonic()
    result = gateway.call("extract", "模板甲", {"text": "a"}, Echo)
    assert result.parsed.value == "过了"
    assert slept == [5.0, 5.0]
    assert len(transport.calls) == 3
    assert time.monotonic() - started < 1

    always = HttpResponse(429, {"error": {"message": "slow down"}}, {"Retry-After": "4"})
    gateway, transport, slept = _gateway(tmp_path, [always, always, always, always])
    with pytest.raises(LLMUnavailable, match="429"):
        gateway.call("extract", "模板甲", {"text": "b"}, Echo)
    assert len(transport.calls) == 3
    assert slept == [4.0, 4.0]


def test_invalid_key_and_no_balance_do_not_retry(tmp_path) -> None:
    gateway, transport, slept = _gateway(
        tmp_path,
        [HttpResponse(401, {"error": {"code": "invalid_api_key", "message": "Incorrect API key"}})],
    )
    with pytest.raises(LLMUnavailable, match="Key 无效"):
        gateway.call("extract", "模板甲", {"text": "a"}, Echo)
    assert len(transport.calls) == 1
    assert slept == []

    gateway, transport, slept = _gateway(
        tmp_path,
        [HttpResponse(402, {"error": {"code": "insufficient_quota", "message": "no balance"}})],
    )
    with pytest.raises(LLMUnavailable, match="无可用余额"):
        gateway.call("extract", "模板甲", {"text": "a"}, Echo)
    assert len(transport.calls) == 1
    assert slept == []


def test_json_repair_once_then_needs_manual(tmp_path) -> None:
    bad = HttpResponse(200, {"choices": [{"message": {"content": "not-json"}}], "usage": {}})
    gateway, transport, _slept = _gateway(tmp_path, [bad, bad, bad])
    result = gateway.call("parse_jd", "模板甲", {"jd": "职责"}, Echo)
    assert result.status == "needs_manual"
    assert result.parsed is None
    assert "needs_manual" in (result.error or "")
    assert len(transport.calls) == 2


def test_replay_missing_cache_has_explicit_error(tmp_path) -> None:
    gateway, transport, _slept = _gateway(tmp_path, [], llm_mode="replay", llm_api_key="")
    with pytest.raises(JobBotError, match=REPLAY_CACHE_MISS) as exc_info:
        gateway.call("explain", "模板甲", {"jd": "原文"}, Echo)
    assert "缓存" in str(exc_info.value)
    assert "没有请求模型" in str(exc_info.value)
    assert len(transport.calls) == 0


def test_logs_and_cache_omit_resume_cookie_and_authorization(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    marker = "简历全文标记ZZZ"
    gateway, transport, _slept = _gateway(tmp_path, [_ok("ok")])
    with caplog.at_level(logging.INFO, logger="jobbot.llm"):
        gateway.call("extract", "模板甲", {"resume": marker, "cookie": "session=hidden-cookie"}, Echo)
    assert marker in str(transport.calls[0]["payload"])
    assert "Authorization" in transport.calls[0]["headers"]  # type: ignore[operator]
    assert marker not in caplog.text
    assert "hidden-cookie" not in caplog.text
    assert "Authorization" not in caplog.text
    assert "Bearer" not in caplog.text
    database = sqlite3.connect(tmp_path / "db" / "jobbot.sqlite")
    stored = "\n".join(str(row) for row in database.execute("SELECT * FROM llm_call"))
    assert marker not in stored
    assert "hidden-cookie" not in stored
    cache_text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "cache" / "llm").glob("*.json"))
    assert marker not in cache_text
    assert "hidden-cookie" not in cache_text
