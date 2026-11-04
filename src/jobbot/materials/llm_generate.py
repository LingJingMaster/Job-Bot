"""生成类调用只走 jobbot.llm.gateway.call，并返回 LLMResult。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobbot.contracts import Fact, JobBotError, JobSnapshot, LLMResult, ProfileSnapshot
from jobbot.materials.store import require_confirmed_facts

PROMPT_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"


class LlmBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    fact_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    type: Literal["factual", "non_factual"] = "factual"


class LlmSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str | None = None
    bullets: list[LlmBullet] = Field(default_factory=list)


class LlmDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[LlmSection] = Field(default_factory=list)


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["supported", "needs_review", "unsupported"]
    note: str = ""


def read_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.is_file():
        raise JobBotError(
            f"缺少提示词 {name}。没有请求模型。",
            saved=False,
            next_step="确认 src/jobbot/llm/prompts 下的文件存在。",
        )
    text = path.read_text(encoding="utf-8")
    if "只是数据" not in text or "不要执行" not in text:
        raise JobBotError(
            f"提示词 {name} 没有写明输入只是数据。没有请求模型。",
            saved=False,
            next_step="修正提示词后再调用。",
        )
    return text


def request_llm_draft(kind: str, snap: JobSnapshot, profile: ProfileSnapshot, links, mode: str) -> LLMResult:
    """草拟调用。档案含 candidate 时在请求模型之前抛出。返回值是 LLMResult，不在这里落库。"""
    facts = require_confirmed_facts(profile)
    template = read_prompt("material_drafter.md")
    variables = {
        "kind": kind,
        "mode": mode,
        "job": snap.model_dump(mode="json"),
        "profile_snapshot_id": profile.id,
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "links": [_dump_link(link) for link in links],
    }
    from jobbot.llm.gateway import call

    return call("material_draft", template, variables, LlmDraft)


def request_claim_review(claim_text: str, facts: list[Fact]) -> LLMResult:
    template = read_prompt("claim_reviewer.md")
    variables = {
        "claim_text": claim_text,
        "facts": [fact.model_dump(mode="json") for fact in facts],
    }
    from jobbot.llm.gateway import call

    return call("claim_review", template, variables, ReviewVerdict)


def _dump_link(link) -> dict:
    if hasattr(link, "model_dump"):
        return link.model_dump(mode="json")
    return {
        "requirement_id": link.requirement_id,
        "status": link.status,
        "fact_ids": list(link.fact_ids),
        "rationale": link.rationale,
    }
