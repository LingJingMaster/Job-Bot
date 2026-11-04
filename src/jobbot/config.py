"""读取 .env.example 中的那一组变量。

缺省值只来自文档示例，不使用作者个人路径、密钥或服务商。
LLM_API_KEY 允许为空，调用方据此降级。DATA_DIR 未设置时为 ./data。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, field_validator

from jobbot.contracts import ConfigError

ENV_KEYS = (
    "DATA_DIR",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MODE",
    "EMBEDDING_MODEL",
    "EMBEDDING_REVISION",
    "COLLECT_MAX_JOBS",
    "COLLECT_MAX_MINUTES",
    "TASK_BUDGET_CALLS",
    "TASK_BUDGET_USD",
    "LOG_LEVEL",
)

DOCUMENTED_DEFAULTS: dict[str, str] = {
    "DATA_DIR": "./data",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    "LLM_MODE": "live",
    "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5",
    "EMBEDDING_REVISION": "",
    "COLLECT_MAX_JOBS": "30",
    "COLLECT_MAX_MINUTES": "20",
    "TASK_BUDGET_CALLS": "200",
    "TASK_BUDGET_USD": "2.0",
    "LOG_LEVEL": "INFO",
}

_EMPTY_MEANS_MISSING = frozenset(
    {
        "DATA_DIR",
        "LLM_MODE",
        "EMBEDDING_MODEL",
        "COLLECT_MAX_JOBS",
        "COLLECT_MAX_MINUTES",
        "TASK_BUDGET_CALLS",
        "TASK_BUDGET_USD",
        "LOG_LEVEL",
    }
)

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})




class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_mode: str
    embedding_model: str
    embedding_revision: str
    collect_max_jobs: int
    collect_max_minutes: int
    task_budget_calls: int
    task_budget_usd: float
    log_level: str

    @field_validator("llm_mode")
    @classmethod
    def mode_is_live_or_replay(cls, value: str) -> str:
        if value not in {"live", "replay"}:
            raise ValueError("LLM_MODE 只能是 live 或 replay")
        return value

    @field_validator("log_level")
    @classmethod
    def known_log_level(cls, value: str) -> str:
        if value not in _LOG_LEVELS:
            raise ValueError("LOG_LEVEL 只能是 DEBUG、INFO、WARNING、ERROR 或 CRITICAL")
        return value

    def embedding_model_path(self) -> Path | None:
        if not self.embedding_revision.strip():
            return None
        return self.data_dir / "models" / self.embedding_revision.strip()

    def embedding_model_exists(self) -> bool:
        path = self.embedding_model_path()
        if path is None or not path.exists():
            return False
        if path.is_file():
            return True
        return path.is_dir() and any(path.iterdir())


def mask_secret(secret: str) -> str:
    if not secret:
        return "（未设置）"
    if len(secret) <= 8:
        return "****"
    return "****" + secret[-4:]


def require_live_endpoint(settings: Settings) -> None:
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", settings.llm_base_url),
            ("LLM_MODEL", settings.llm_model),
        )
        if not value.strip()
    ]
    if missing:
        joined = "、".join(missing)
        raise ConfigError(
            f"缺少必需配置：{joined}。没有请求模型，也没有填入默认服务商。"
            "下一步：在 .env 中填写 OpenAI 兼容地址和模型名。",
            saved=False,
            next_step=f"填写 {joined} 后重试。",
        )


def load_config(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    load_env_file: bool = True,
) -> Settings:
    """环境变量优先于 env 文件，env 文件优先于文档示例中的默认值。"""
    source = os.environ if environ is None else environ
    file_values: dict[str, str] = {}
    if load_env_file:
        path = env_file if env_file is not None else _discover_env_file()
        if path is not None and path.is_file():
            file_values = _parse_env_file(path)
    raw: dict[str, str] = {}
    for key in ENV_KEYS:
        if key in source:
            raw[key] = source[key]
        elif key in file_values:
            raw[key] = file_values[key]
        else:
            raw[key] = DOCUMENTED_DEFAULTS[key]
        if key in _EMPTY_MEANS_MISSING and raw[key].strip() == "":
            raise ConfigError(
                f"缺少必需配置：{key} 为空。没有使用作者个人默认值。"
                "相关数据未保存。下一步：按 .env.example 填写该项。",
                saved=False,
                next_step=f"为 {key} 填写有效值。",
            )
    data_dir = Path(raw["DATA_DIR"]).expanduser()
    if not data_dir.is_absolute():
        data_dir = (Path.cwd() / data_dir).resolve()
    try:
        collect_max_jobs = _positive_int("COLLECT_MAX_JOBS", raw["COLLECT_MAX_JOBS"])
        collect_max_minutes = _positive_int("COLLECT_MAX_MINUTES", raw["COLLECT_MAX_MINUTES"])
        task_budget_calls = _non_negative_int("TASK_BUDGET_CALLS", raw["TASK_BUDGET_CALLS"])
        task_budget_usd = _non_negative_float("TASK_BUDGET_USD", raw["TASK_BUDGET_USD"])
        return Settings(
            data_dir=data_dir,
            llm_base_url=raw["LLM_BASE_URL"].strip(),
            llm_api_key=raw["LLM_API_KEY"].strip(),
            llm_model=raw["LLM_MODEL"].strip(),
            llm_mode=raw["LLM_MODE"].strip(),
            embedding_model=raw["EMBEDDING_MODEL"].strip(),
            embedding_revision=raw["EMBEDDING_REVISION"].strip(),
            collect_max_jobs=collect_max_jobs,
            collect_max_minutes=collect_max_minutes,
            task_budget_calls=task_budget_calls,
            task_budget_usd=task_budget_usd,
            log_level=raw["LOG_LEVEL"].strip().upper(),
        )
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(
            f"配置无法解析：{exc}。相关数据未保存。下一步：按 .env.example 修正该项。",
            saved=False,
            next_step="修正无效配置后重试。",
        ) from exc


def _positive_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(
            f"缺少必需配置：{name} 不是整数（当前为 {value!r}）。下一步：填写正整数。",
            saved=False,
            next_step=f"把 {name} 改成正整数。",
        ) from exc
    if parsed < 1:
        raise ConfigError(
            f"缺少必需配置：{name} 必须大于 0。下一步：按 .env.example 填写。",
            saved=False,
            next_step=f"把 {name} 改成正整数。",
        )
    return parsed


def _non_negative_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(
            f"缺少必需配置：{name} 不是整数（当前为 {value!r}）。下一步：填写非负整数。",
            saved=False,
            next_step=f"把 {name} 改成非负整数。",
        ) from exc
    if parsed < 0:
        raise ConfigError(
            f"缺少必需配置：{name} 不能为负数。下一步：填写 0 或正整数。",
            saved=False,
            next_step=f"把 {name} 改成非负整数。",
        )
    return parsed


def _non_negative_float(name: str, value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(
            f"缺少必需配置：{name} 不是数字（当前为 {value!r}）。下一步：填写非负金额。",
            saved=False,
            next_step=f"把 {name} 改成非负数字。",
        ) from exc
    if parsed < 0:
        raise ConfigError(
            f"缺少必需配置：{name} 不能为负数。下一步：填写 0 或正数。",
            saved=False,
            next_step=f"把 {name} 改成非负数字。",
        )
    return parsed


def _discover_env_file() -> Path | None:
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        if (directory / "pyproject.toml").is_file():
            return candidate if candidate.is_file() else None
    return None


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("#"):
            value = ""
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            comment_at = value.find(" #")
            if comment_at != -1:
                value = value[:comment_at].rstrip()
        if key in DOCUMENTED_DEFAULTS:
            values[key] = value
    return values
