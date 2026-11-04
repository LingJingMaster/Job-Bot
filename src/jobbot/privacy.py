"""云端外发确认、发送前脱敏开关，以及数据目录清除。

确认状态写在 DATA_DIR/privacy/cloud_outbound.json，不写入数据库。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobbot.contracts import JobBotError
from jobbot.llm.redact import redact_text, redact_value

OUTBOUND_FIELDS = ("简历事实", "JD 文本")
PRIVACY_FILE = Path("privacy") / "cloud_outbound.json"
UNCONFIRMED_CLOUD_CALL = (
    "尚未确认云端外发。将发送的字段是简历事实和 JD 文本，它们会发往所选 LLM 提供方。"
    "没有发出真实请求，也没有请求模型。"
)

_CLEAR_TARGETS = (
    ("db", "数据库（data/db）"),
    ("raw", "原始文件（data/raw）"),
    ("cache", "缓存（data/cache）"),
    ("embeddings", "向量（data/embeddings）"),
    ("vectors", "向量（data/vectors）"),
    ("exports", "导出（data/exports）"),
)


@dataclass(frozen=True)
class PrivacyPreferences:
    confirmed: bool = False
    redact_before_send: bool = False


@dataclass(frozen=True)
class ClearCheck:
    label: str
    empty: bool
    remaining: int


@dataclass(frozen=True)
class ClearResult:
    checks: tuple[ClearCheck, ...]
    refused: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused and all(check.empty for check in self.checks)

    def text(self) -> str:
        if self.refused:
            return f"{self.message}\n没有删除仓库源码。"
        lines = ["清除数据核对结果："]
        for check in self.checks:
            if check.empty:
                lines.append(f"{check.label}：已清空。")
            else:
                lines.append(f"{check.label}：未清空，仍有 {check.remaining} 个文件。")
        if self.ok:
            lines.append(
                "核对通过。数据库、原始文件、缓存、向量和导出目录都为空。没有删除仓库源码。"
            )
        else:
            lines.append("核对未通过。没有删除仓库源码。")
        return "\n".join(lines)


def privacy_path(data_dir: Path) -> Path:
    return data_dir / PRIVACY_FILE


def load_privacy_preferences(data_dir: Path) -> PrivacyPreferences:
    path = privacy_path(data_dir)
    if not path.is_file():
        return PrivacyPreferences()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return PrivacyPreferences()
    if not isinstance(payload, dict):
        return PrivacyPreferences()
    return PrivacyPreferences(
        confirmed=payload.get("confirmed") is True,
        redact_before_send=payload.get("redact_before_send") is True,
    )


def confirm_cloud_outbound(data_dir: Path) -> PrivacyPreferences:
    current = load_privacy_preferences(data_dir)
    updated = PrivacyPreferences(confirmed=True, redact_before_send=current.redact_before_send)
    _write_preferences(data_dir, updated)
    return updated


def set_redact_before_send(data_dir: Path, enabled: bool) -> PrivacyPreferences:
    current = load_privacy_preferences(data_dir)
    updated = PrivacyPreferences(confirmed=current.confirmed, redact_before_send=enabled)
    _write_preferences(data_dir, updated)
    return updated


def ensure_cloud_confirmed(data_dir: Path) -> None:
    if load_privacy_preferences(data_dir).confirmed:
        return
    raise JobBotError(
        UNCONFIRMED_CLOUD_CALL,
        saved=False,
        next_step="打开设置页，核对将发送的字段并确认。",
    )


def redacted_outbound(
    data_dir: Path,
    template: str,
    variables: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if not load_privacy_preferences(data_dir).redact_before_send:
        return template, variables
    redacted = redact_value(variables)
    if not isinstance(redacted, dict):
        return redact_text(template), variables
    return redact_text(template), redacted


def clear_user_data(data_dir: Path) -> ClearResult:
    """删除数据目录中的数据库、原始文件、缓存、向量和导出，并核对是否为空。"""
    root = _resolve_data_dir(data_dir)
    refused = _refuse_reason(root)
    if refused is not None:
        return ClearResult(checks=(), refused=True, message=refused)
    checks: list[ClearCheck] = []
    for name, label in _CLEAR_TARGETS:
        _wipe_named_directory(root, name)
        remaining = _leftover_files(root / name)
        checks.append(ClearCheck(label=label, empty=not remaining, remaining=len(remaining)))
    return ClearResult(checks=tuple(checks))


def _resolve_data_dir(data_dir: Path) -> Path:
    path = data_dir.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _refuse_reason(data_dir: Path) -> str | None:
    if data_dir == Path(data_dir.anchor):
        return "拒绝清除：数据目录是文件系统根目录。没有删除任何文件。"
    if (data_dir / "pyproject.toml").is_file() or (data_dir / ".git").exists():
        return "拒绝清除：数据目录指向仓库本身。没有删除任何文件。"
    if (data_dir / "src" / "jobbot").is_dir():
        return "拒绝清除：数据目录里有项目源码。没有删除任何文件。"
    return None


def _wipe_named_directory(data_dir: Path, name: str) -> None:
    target = data_dir / name
    if not data_dir.is_dir():
        return
    if target.is_symlink():
        target.unlink()
    elif target.is_file():
        target.unlink()
    elif target.is_dir():
        if not _is_within(data_dir, target.resolve()):
            raise JobBotError(
                f"拒绝清除：{name} 指向数据目录之外。没有继续删除。",
                saved=False,
                next_step="检查 DATA_DIR 下的目录是否被挂到仓库外面。",
            )
        for child in list(target.iterdir()):
            _unlink_tree(child)
    target.mkdir(parents=True, exist_ok=True)


def _is_within(parent: Path, child: Path) -> bool:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    return child_resolved == parent_resolved or parent_resolved in child_resolved.parents


def _unlink_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for child in list(path.iterdir()):
        _unlink_tree(child)
    path.rmdir()


def _leftover_files(path: Path) -> list[Path]:
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or path.is_file():
        return [path]
    leftovers: list[Path] = []
    for child in path.rglob("*"):
        if child.is_symlink() or child.is_file():
            leftovers.append(child)
    return leftovers


def _write_preferences(data_dir: Path, preferences: PrivacyPreferences) -> None:
    path = privacy_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "confirmed": preferences.confirmed,
        "redact_before_send": preferences.redact_before_send,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
