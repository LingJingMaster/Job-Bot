"""与 docs/03 对齐的枚举、模型与错误类型。

这里只放数据结构与校验，不放采集、档案、匹配、材料或网关的业务流程。
null 表示未知或未提供；空数组表示确认没有。主键由仓储生成 uuid4().hex。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TEXT_CHARS = 100_000
EXPLANATION_SCORE_NOTE = "排序分不是录用概率"

FactCategory = Literal["education", "work", "project", "skill", "award", "certificate", "other"]
Contribution = Literal["support", "participate", "own", "push", "lead"]
ApplicationStage = Literal[
    "interested",
    "preparing",
    "ready",
    "applied",
    "communicating",
    "interviewing",
    "offer",
    "closed",
]
HardResult = Literal["pass", "fail", "needs_review"]
RequirementType = Literal["hard", "core_skill", "responsibility", "domain", "bonus", "other"]
EvidenceStatus = Literal["covered", "weak", "missing", "not_applicable"]
DocumentKind = Literal["resume", "jd_paste", "jd_import"]
ParseStatus = Literal["pending", "ok", "failed"]
FactStatus = Literal["candidate", "confirmed", "retracted"]
CollectMode = Literal["assist", "auto", "manual"]
CollectRunStatus = Literal["running", "finished", "stopped", "failed"]
CollectCommandName = Literal["start", "stop"]
Platform = Literal["boss", "manual"]
JobSource = Literal["boss", "manual"]
Availability = Literal["active", "closed", "unknown"]
SalaryUnit = Literal["month_k", "day_yuan", "hour_yuan", "unknown"]
JDParseStatus = Literal["ok", "needs_manual", "failed"]
MatchMethod = Literal["b0", "b1", "b2", "b3", "llm"]
LabelRound = Literal["first", "recheck", "second"]
HardLabel = Literal["pass", "fail", "unclear"]
FeedbackKind = Literal["save", "reject"]
FeedbackReason = Literal[
    "location",
    "pay",
    "duty",
    "company",
    "experience",
    "duplicate",
    "other",
]
MaterialKind = Literal["resume", "ats_text", "opening_msg"]
MaterialStatus = Literal["draft", "needs_input", "verified", "approved", "superseded"]
ClaimType = Literal["factual", "non_factual"]
CheckStatus = Literal["supported", "unsupported", "needs_review"]
ClaimReason = Literal[
    "number_mismatch",
    "verb_escalation",
    "unknown_skill",
    "date_conflict",
    "org_mismatch",
    "no_fact",
    "semantic_unsupported",
]
CostKind = Literal["actual", "estimated", "unavailable"]
UnknownPolicy = Literal["needs_review", "pass", "fail"]
CapabilityState = Literal["可用", "降级", "不可用"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_overlong_text(value: str | None) -> str | None:
    if value is not None and len(value) > MAX_TEXT_CHARS:
        raise ValueError(
            f"文本长度为 {len(value)}，超过 {MAX_TEXT_CHARS} 字符，已拒绝且未截断"
        )
    return value


def _reject_zero_salary(value: int | None, field_name: str) -> int | None:
    if value == 0:
        raise ValueError(f"{field_name} 不能为 0；未知请用 null")
    return value


class JobBotError(Exception):
    """服务层错误。界面应展示发生了什么、数据是否保存、下一步。"""

    def __init__(self, message: str, *, saved: bool = False, next_step: str = "") -> None:
        self.saved = saved
        self.next_step = next_step
        super().__init__(message)

    def user_text(self) -> str:
        saved_text = "相关数据已保存。" if self.saved else "相关数据未保存。"
        next_text = f"下一步：{self.next_step}" if self.next_step else "下一步：查看提示后重试。"
        return f"{self}{saved_text}{next_text}"


class ConfigError(JobBotError):
    """缺少或无法解析必需配置。"""


class LLMUnavailable(JobBotError):
    """无余额、Key 无效，或暂时性错误重试后仍不可用。"""


class LLMSchemaError(JobBotError):
    """模型输出在一次修复后仍不符合 schema。"""


class BudgetExceeded(JobBotError):
    """单个任务的调用次数或金额超出预算。"""


class CollectorBlocked(JobBotError):
    """采集器已停止。stop_reason 写入调用方，本异常不触发自动重试。"""

    def __init__(
        self,
        message: str,
        stop_reason: str,
        *,
        saved: bool = True,
        next_step: str = "",
    ) -> None:
        self.stop_reason = stop_reason
        super().__init__(message, saved=saved, next_step=next_step)


class ParseError(JobBotError):
    """文本或接口响应无法按契约解析。"""


class ApprovalBlocked(JobBotError):
    """仍有 unsupported 或 needs_review 的 Claim，不能批准或导出终稿。"""


class StaleProfile(JobBotError):
    """操作使用了过期的档案版本。"""


class SourceRef(ContractModel):
    """引用原文。简历用 locator（页码/段落）；JD 用 span，区间为 [start, end)。"""

    source_id: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    span: list[int] | None = None

    @model_validator(mode="after")
    def span_is_half_open_pair(self) -> SourceRef:
        if self.span is not None and len(self.span) != 2:
            raise ValueError("span 必须是长度为 2 的 [start, end)")
        return self


class HardConstraint(ContractModel):
    field: Literal["city", "degree", "experience_years", "salary_min", "salary_max", "keyword_exclude"]
    operator: Literal["eq", "in", "gte", "lte", "not_contains"]
    value: Any
    unknown_policy: UnknownPolicy = "needs_review"


class Metric(ContractModel):
    text: str
    value: float | None = None
    unit: str | None = None


class FactStructured(ContractModel):
    org: str | None = None
    role: str | None = None
    start: str | None = None
    end: str | None = None
    skills: list[str] | None
    metrics: list[Metric] | None


class FactCandidate(ContractModel):
    category: FactCategory
    statement: str
    structured: FactStructured
    contribution: Contribution | None = None
    is_estimate: bool = False
    source_ref: SourceRef

    @model_validator(mode="after")
    def statement_within_limit(self) -> FactCandidate:
        _reject_overlong_text(self.statement)
        return self


class FactEdits(ContractModel):
    category: FactCategory | None = None
    statement: str | None = None
    structured: FactStructured | None = None
    contribution: Contribution | None = None
    is_estimate: bool | None = None
    estimate_note: str | None = None

    @model_validator(mode="after")
    def statement_within_limit(self) -> FactEdits:
        _reject_overlong_text(self.statement)
        return self


class Fact(ContractModel):
    id: str
    profile_id: str
    fact_key: str
    revision: int
    category: FactCategory
    statement: str
    structured: FactStructured | None = None
    source_ref: SourceRef | None = None
    status: FactStatus
    contribution: Contribution | None = None
    is_estimate: bool = False
    estimate_note: str | None = None
    confirmed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ProfileSnapshot(ContractModel):
    id: str
    profile_id: str
    revision: int
    fact_ids: list[str]
    sha256: str
    created_at: str | None = None
    updated_at: str | None = None


class Preference(ContractModel):
    id: str | None = None
    profile_id: str
    revision: int
    hard: list[HardConstraint] | None = None
    soft: list[dict[str, Any]] | None = None
    search_terms: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CollectLimits(ContractModel):
    max_jobs: int = Field(ge=1)
    max_minutes: int = Field(ge=1)


class RawDetail(ContractModel):
    external_id: str
    raw: dict[str, Any]


class Salary(ContractModel):
    salary_text: str | None = None
    min: int | None = None
    max: int | None = None
    salary_unit: SalaryUnit | None = None
    months: int | None = None

    @model_validator(mode="after")
    def unknown_is_null_not_zero(self) -> Salary:
        _reject_zero_salary(self.min, "min")
        _reject_zero_salary(self.max, "max")
        _reject_zero_salary(self.months, "months")
        return self


class Job(ContractModel):
    id: str
    platform: Platform
    external_id: str | None = None
    url: str | None = None
    dedup_group: str | None = None
    latest_snapshot_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class JobSnapshotDraft(ContractModel):
    platform: Platform
    external_id: str | None = None
    url: str | None = None
    raw_path: str | None = None
    content_hash: str | None = None
    title: str | None = None
    company: str | None = None
    city: str | None = None
    district: str | None = None
    salary_text: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_unit: SalaryUnit | None = None
    salary_months: int | None = None
    experience_text: str | None = None
    degree_text: str | None = None
    description: str | None = None
    hr_title: str | None = None
    hr_active_text: str | None = None
    availability: Availability = "unknown"
    source: JobSource

    @model_validator(mode="after")
    def text_and_salary(self) -> JobSnapshotDraft:
        _reject_overlong_text(self.description)
        _reject_zero_salary(self.salary_min, "salary_min")
        _reject_zero_salary(self.salary_max, "salary_max")
        _reject_zero_salary(self.salary_months, "salary_months")
        return self


class JobSnapshot(JobSnapshotDraft):
    id: str
    job_id: str
    run_id: str | None = None
    fetched_at: str
    content_hash: str
    created_at: str | None = None
    updated_at: str | None = None


class RequirementNormalized(ContractModel):
    skills: list[str]
    degree: str | None = None
    years_min: int | None = None


class Requirement(ContractModel):
    id: str
    text: str
    type: RequirementType
    weight: int
    inferred: bool = False
    source_ref: SourceRef
    normalized: RequirementNormalized

    @model_validator(mode="after")
    def weight_and_inferred(self) -> Requirement:
        if self.weight not in (1, 2, 3):
            raise ValueError("weight 只能是 1、2 或 3")
        if self.inferred and self.type == "hard":
            raise ValueError("inferred 要求不得作为硬条件")
        return self


class JDParse(ContractModel):
    id: str | None = None
    snapshot_id: str | None = None
    llm_call_id: str | None = None
    status: JDParseStatus
    requirements: list[Requirement] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class HardCheck(ContractModel):
    hard_result: HardResult
    hard_reasons: list[str]
    unknown_fields: list[str]


class TfidfConfig(ContractModel):
    analyzer: Literal["char"] = "char"
    ngram_range: tuple[int, int] = (2, 4)
    norm: Literal["l2"] = "l2"
    sublinear_tf: bool | None = None
    min_df: int | float | None = None


class TfidfIndex(ContractModel):
    corpus_ids: list[str]
    config: TfidfConfig
    vocab_path: str | None = None


class Features(ContractModel):
    """缺失特征用 null，不用 0 表示不满足。"""

    f_sem: float | None = None
    f_skill: float | None = None
    f_resp: float | None = None
    f_pref: float | None = None
    f_tfidf: float | None = None
    f_exp: float | None = None


class MatchResult(ContractModel):
    id: str | None = None
    run_id: str
    snapshot_id: str
    hard_result: HardResult
    hard_reasons: list[str] | None = None
    score: float | None = Field(default=None, description=EXPLANATION_SCORE_NOTE)
    rank: int | None = None
    components: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    unknown_fields: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MatchRun(ContractModel):
    id: str
    profile_snapshot_id: str
    preference_id: str
    method: MatchMethod
    config: dict[str, Any]
    config_hash: str
    n_jobs: int
    results: list[MatchResult] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class Explanation(ContractModel):
    matched: list[str]
    gaps: list[str]
    unknowns: list[str]
    used_template: bool
    score_note: str = EXPLANATION_SCORE_NOTE


class EvidenceLink(ContractModel):
    requirement_id: str
    status: EvidenceStatus
    fact_ids: list[str]
    rationale: str

    @model_validator(mode="after")
    def coverage_rules(self) -> EvidenceLink:
        if len(self.rationale) > 60:
            raise ValueError("rationale 不能超过 60 字")
        if self.status == "covered" and len(self.fact_ids) == 0:
            raise ValueError("covered 必须至少有一个 fact_id")
        if self.status == "not_applicable" and not self.rationale.strip():
            raise ValueError("not_applicable 需要理由")
        return self


class DraftBullet(ContractModel):
    text: str
    fact_ids: list[str]
    requirement_ids: list[str]
    type: ClaimType

    @model_validator(mode="after")
    def factual_needs_facts(self) -> DraftBullet:
        if self.type == "factual" and len(self.fact_ids) == 0:
            raise ValueError("factual bullet 必须带 fact_ids，否则视为 unsupported")
        return self


class DraftSection(ContractModel):
    heading: str | None = None
    bullets: list[DraftBullet]


class MaterialDraft(ContractModel):
    sections: list[DraftSection]


class Question(ContractModel):
    id: str | None = None
    material_id: str | None = None
    requirement_id: str
    text: str
    answer: str | None = None
    answered_at: str | None = None
    resulting_fact_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Material(ContractModel):
    id: str
    profile_snapshot_id: str
    snapshot_id: str
    kind: MaterialKind
    mode: str
    revision: int
    content: str
    status: MaterialStatus
    content_hash: str
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @model_validator(mode="after")
    def mode_and_length(self) -> Material:
        _reject_overlong_text(self.content)
        allowed = {"pipeline", "polish", "raw"}
        if self.mode not in allowed and not self.mode.startswith("ablation_"):
            raise ValueError("mode 必须是 pipeline、polish、raw 或 ablation_*")
        return self


class Claim(ContractModel):
    id: str | None = None
    material_id: str
    span_start: int
    span_end: int
    text: str
    type: ClaimType
    fact_ids: list[str] | None = None
    requirement_ids: list[str] | None = None
    check_status: CheckStatus
    check_detail: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClaimCheck(ContractModel):
    claim_id: str
    check_status: CheckStatus
    reasons: list[ClaimReason]
    detail: dict[str, Any]


class Budget(ContractModel):
    max_calls: int = Field(ge=0)
    max_usd: float = Field(ge=0)
    calls_used: int = Field(default=0, ge=0)
    usd_used: float = Field(default=0, ge=0)


class LLMResult(ContractModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: Literal["ok", "needs_manual", "failed"]
    purpose: str
    parsed: Any = None
    error: str | None = None
    cache_hit: bool = False
    llm_call_id: str | None = None
    cost: float | None = None
    cost_kind: CostKind | None = None


class Capability(ContractModel):
    name: str
    status: CapabilityState
    reason: str
