from pathlib import Path

import pytest

from jobbot.config import DOCUMENTED_DEFAULTS, load_config, mask_secret, require_live_endpoint
from jobbot.contracts import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def test_example_file_keeps_key_empty_and_documented_defaults() -> None:
    settings = load_config(ROOT / ".env.example", environ={}, load_env_file=True)
    assert settings.llm_api_key == ""
    assert settings.llm_base_url == ""
    assert settings.llm_model == ""
    assert settings.llm_mode == "live"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_revision == ""
    assert settings.collect_max_jobs == 30
    assert settings.task_budget_calls == 200
    assert settings.task_budget_usd == 2.0
    assert settings.data_dir.name == "data"
    assert DOCUMENTED_DEFAULTS["DATA_DIR"] == "./data"
    assert DOCUMENTED_DEFAULTS["LLM_API_KEY"] == ""


def test_missing_required_value_raises_readable_config_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(environ={"LLM_MODE": ""}, load_env_file=False)
    message = exc_info.value.user_text()
    assert "LLM_MODE" in message
    assert "未保存" in message
    assert "下一步" in message


def test_invalid_mode_names_allowed_values() -> None:
    with pytest.raises(ConfigError, match="live 或 replay"):
        load_config(environ={"LLM_MODE": "turbo"}, load_env_file=False)


def test_empty_api_key_is_allowed_and_masked() -> None:
    settings = load_config(environ={"LLM_API_KEY": ""}, load_env_file=False)
    assert settings.llm_api_key == ""
    assert mask_secret("") == "（未设置）"
    secret = "local-" + ("q" * 16)
    masked = mask_secret(secret)
    assert secret not in masked
    assert masked.endswith(secret[-4:])
    assert masked.startswith("****")


def test_live_endpoint_does_not_invent_a_provider() -> None:
    settings = load_config(environ={"LLM_API_KEY": "present"}, load_env_file=False)
    with pytest.raises(ConfigError, match="LLM_BASE_URL") as exc_info:
        require_live_endpoint(settings)
    assert "没有填入默认服务商" in str(exc_info.value)
