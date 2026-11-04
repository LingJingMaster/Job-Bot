"""别名字典 v1。比较时归并，入库原文不改写。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from jobbot.contracts import JobBotError

ALIAS_PATH = Path(__file__).with_name("aliases.json")


class _AliasEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical: str
    aliases: list[str]


class AliasDictionary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int
    cities: list[_AliasEntry]
    skills: list[_AliasEntry]


@lru_cache(maxsize=1)
def load_aliases() -> AliasDictionary:
    try:
        payload = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
        dictionary = AliasDictionary.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise JobBotError(
            "别名字典 aliases.json 无法读取。城市和技能仍按原词比较，没有改写已保存文本。",
            saved=False,
            next_step="恢复 src/jobbot/jobs/aliases.json 后重试。",
        ) from exc
    if dictionary.version != 1:
        raise JobBotError(
            f"别名字典版本是 {dictionary.version}，当前只支持 v1。没有改写已保存文本。",
            saved=False,
            next_step="使用 version 为 1 的 aliases.json。",
        )
    return dictionary


def _city_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in load_aliases().cities:
        for alias in entry.aliases:
            mapping[alias.casefold()] = entry.canonical
        mapping[entry.canonical.casefold()] = entry.canonical
    return mapping


def _skill_groups() -> tuple[dict[str, str], dict[str, list[str]]]:
    lookup: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for entry in load_aliases().skills:
        names = list(dict.fromkeys([entry.canonical, *entry.aliases]))
        groups[entry.canonical] = names
        for name in names:
            lookup[name.casefold()] = entry.canonical
    return lookup, groups


def canonical_city(name: str | None) -> str | None:
    if name is None:
        return None
    stripped = name.strip()
    if not stripped:
        return None
    return _city_map().get(stripped.casefold(), stripped)


def lookup_skill(term: str) -> str | None:
    lookup, _groups = _skill_groups()
    return lookup.get(term.strip().casefold())


def canonical_skill(term: str) -> str:
    stripped = term.strip()
    found = lookup_skill(stripped)
    return found if found is not None else stripped


def equivalent_terms(term: str) -> list[str]:
    """已知技能返回整组别名；未知词只返回自身。Java 不会带上 JavaScript。"""
    canonical = lookup_skill(term)
    if canonical is None:
        return [term.strip()] if term.strip() else []
    _lookup, groups = _skill_groups()
    return list(groups[canonical])


def skills_equivalent(left: str, right: str) -> bool:
    left_canonical = lookup_skill(left)
    right_canonical = lookup_skill(right)
    if left_canonical is None or right_canonical is None:
        return left.strip().casefold() == right.strip().casefold()
    return left_canonical == right_canonical
