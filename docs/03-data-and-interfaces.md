# 数据与接口契约（接口冻结文件）

本文件是跨开发者的规范。字段/枚举/函数签名的变更须同步更新 `contracts.py`、`schema.sql`、测试，并在 `docs/CHANGES.md` 记录。以下 Python 片段是**规范性签名**，不是已有实现。

## 1. 公共规范

- 主键：文本 UUID（`uuid4().hex`）。时间：UTC ISO-8601 字符串，展示按 Asia/Shanghai。
- 单用户，无 `owner_id`；若未来多用户再加（见 07 ADR-010）。
- **null = 未知/未提供；空数组 = 确认没有**。不得混用。例：薪资未知为 null，不得写 0。
- 薪资：保留原文 `salary_text`；结构化为 `min`/`max`（整数，单位见 `salary_unit`: `month_k`=千元/月、`day_yuan`=元/天、`hour_yuan`=元/时、`unknown`），`months`（薪数，如 13，缺失 null）。无法解析→全 null 并保留原文，**不猜**。
- 文本长度：JD、简历正文各 ≤ 100,000 字符；超限拒绝并提示，不静默截断事实。
- 引用：任何引用原文的字段为 `{source_id, locator, excerpt}`；`locator` 对简历为页码/段落序号，对 JD 为字符区间 `[start,end)`。校验引用时要求 excerpt 与源文本对应位置一致，否则该引用无效。
- LLM 的 `confidence` 仅作提示，不能替代证据或授权。

## 2. SQLite 表

`PRAGMA foreign_keys=ON; journal_mode=WAL`。除注明外，所有表有 `id TEXT PK, created_at, updated_at`。

| 表 | 关键字段 | 约束/说明 |
|---|---|---|
| document | kind(`resume`/`jd_paste`/`jd_import`), path, sha256, mime, size, text, parse_status(`pending`/`ok`/`failed`), parse_error | 原文件不可覆盖；同 sha256 提示已有 |
| profile | name, current_revision | 一人可有多个 profile（实验时每位同学一个） |
| fact | profile_id, fact_key(稳定标识), revision, category, statement, structured JSON, source_ref JSON, status, contribution(`support`/`participate`/`own`/`push`/`lead`/null), is_estimate(0/1), estimate_note, confirmed_at | status=`candidate`/`confirmed`/`retracted`；修改产生新 revision，旧行保留；`category` 见 §3 |
| profile_snapshot | profile_id, revision, fact_ids JSON, sha256 | 确认档案的不可变快照；匹配与材料引用它 |
| preference | profile_id, revision, hard JSON, soft JSON, search_terms JSON | `hard` 为条件数组（见 §3） |
| collect_run | mode(`assist`/`auto`/`manual`), keyword?, city?, params JSON, status(`running`/`finished`/`stopped`/`failed`), stop_reason?, started_at, finished_at, n_seen, n_ok, n_fail, collector_version | UI 通过它读取进度 |
| collect_command | run_id, command(`start`/`stop`), payload JSON, handled_at? | UI→采集器；采集器轮询 |
| job | platform(`boss`/`manual`), external_id?, url?, dedup_group?, latest_snapshot_id | 有 external_id 时 `UNIQUE(platform, external_id)` |
| job_snapshot | job_id, run_id?, fetched_at, raw_path?, content_hash, title, company, city, district?, salary_text?, salary_min?, salary_max?, salary_unit, salary_months?, experience_text?, degree_text?, description, hr_title?, hr_active_text?, availability(`active`/`closed`/`unknown`), source(`boss`/`manual`) | **不可变**；`UNIQUE(job_id, content_hash)` |
| jd_parse | snapshot_id, llm_call_id, status(`ok`/`needs_manual`/`failed`), requirements JSON | 见 §5.2；同一快照可有多个解析（不同模型/提示），取最新 ok |
| match_run | profile_snapshot_id, preference_id, method(`b0`/`b1`/`b2`/`b3`/`llm`), config JSON, config_hash, created_at, n_jobs | 输入集合固定；重算创建新记录 |
| match_result | run_id, snapshot_id, hard_result(`pass`/`fail`/`needs_review`), hard_reasons JSON, score?, rank?, components JSON, evidence JSON, unknown_fields JSON | `score` 不是概率；`fail` 行也保存 |
| label | profile_id, snapshot_id, rating(0–3)?, hard_label(`pass`/`fail`/`unclear`)?, labeler, labeled_at, note?, round(`first`/`recheck`/`second`) | `UNIQUE(profile_id, snapshot_id, labeler, round)`；标注期间不关联模型输出 |
| feedback | snapshot_id, kind(`save`/`reject`), reason(`location`/`pay`/`duty`/`company`/`experience`/`duplicate`/`other`) | 用户未操作不是负标签 |
| material | profile_snapshot_id, snapshot_id, kind(`resume`/`ats_text`/`opening_msg`), mode(`pipeline`/`polish`/`raw`/`ablation_*`), revision, content, status, content_hash, parent_id? | status=`draft`/`needs_input`/`verified`/`approved`/`superseded`；`approved` 后正文不可变，改动产生新 revision |
| claim | material_id, span_start, span_end, text, type(`factual`/`non_factual`), fact_ids JSON, requirement_ids JSON, check_status(`supported`/`unsupported`/`needs_review`), check_detail JSON | 事实性陈述必须有 fact_ids 才可 `supported` |
| question | material_id, requirement_id, text, answer?, answered_at, resulting_fact_id? | 每轮 ≤ 5；跳过不补造事实 |
| application | job_id, snapshot_id, stage, material_id?, note, closed_reason? | `UNIQUE(job_id)`；阶段见 §3 |
| application_event | application_id, from_stage, to_stage, reason?, origin(`manual`), at | 阶段更正记录旧值、新值、原因 |
| llm_call | purpose, model, template_hash, input_ref, tokens_in, tokens_out, latency_ms, cost, cost_kind(`actual`/`estimated`/`unavailable`), status, cache_hit(0/1), request_id? | 不存原文 |
| experiment_run | experiment(`E0`…`E5`), config JSON, git_commit, started_at, finished_at, results_path | 逐条结果存文件（见 04） |

必须建索引：`job(platform, external_id)`、`job_snapshot(job_id, fetched_at)`、`fact(profile_id, status)`、`match_result(run_id, rank)`、`label(profile_id)`、`llm_call(purpose, created_at)`。

## 3. 枚举与小结构

```python
FactCategory = Literal["education","work","project","skill","award","certificate","other"]
Contribution = Literal["support","participate","own","push","lead"]      # 支持/参与/负责/推动/主导
ApplicationStage = Literal["interested","preparing","ready","applied","communicating",
                           "interviewing","offer","closed"]               # 全部由用户手动设置
HardResult = Literal["pass","fail","needs_review"]
RequirementType = Literal["hard","core_skill","responsibility","domain","bonus","other"]
EvidenceStatus = Literal["covered","weak","missing","not_applicable"]

class HardConstraint(BaseModel):
    field: Literal["city","degree","experience_years","salary_min","salary_max","keyword_exclude"]
    operator: Literal["eq","in","gte","lte","not_contains"]
    value: Any
    unknown_policy: Literal["needs_review","pass","fail"] = "needs_review"
```

**阶段规则**：任意阶段可由用户改为任意其他阶段，必须记录 `application_event`；`applied` 表示用户声明已在平台投递，系统不核验；没有“自动推进”。

## 4. 模块接口（Python 签名）

所有函数返回 pydantic 对象或抛 `JobBotError` 子类；不得把自由文本当作结果。LLM 相关函数在 `LLMUnavailable` 时抛出，由上层降级。

```python
# collector/
class RawDetail(BaseModel):          # 对应详情接口响应的最小拷贝，见 §6
    external_id: str; raw: dict
def parse_detail(raw: dict) -> JobSnapshotDraft: ...      # 纯函数，无 IO；字段缺失给 None，不猜
def start_run(mode: Literal["assist","auto"], keyword: str|None, limits: CollectLimits) -> str: ...  # 写 collect_command
def stop_run(run_id: str) -> None: ...
def import_manual(text: str|None=None, json_obj: dict|None=None) -> JobSnapshot: ...

# jobs/
def parse_salary(text: str|None) -> Salary: ...           # §1 规则；覆盖 "20-30K·13薪""200-300元/天""面议"
def upsert_snapshot(draft: JobSnapshotDraft) -> tuple[Job, JobSnapshot, bool]: ...  # bool=是否新快照
def parse_jd(snapshot_id: str) -> JDParse: ...            # LLM；schema 见 §5.2；失败→status=needs_manual
def hard_filter(snapshot: JobSnapshot, pref: Preference, facts: list[Fact]) -> HardCheck: ...

# profile/
def extract_facts(document_id: str) -> list[FactCandidate]: ...   # LLM；每条带 source_ref；校验 excerpt
def confirm_fact(fact_id: str, edits: FactEdits|None=None) -> Fact: ...
def retract_fact(fact_id: str, reason: str) -> list[str]: ...      # 返回受影响的 material_id
def freeze_profile(profile_id: str) -> ProfileSnapshot: ...        # 仅含 confirmed 事实

# matching/
def build_tfidf(corpus_ids: list[str], cfg: TfidfConfig) -> TfidfIndex: ...   # 配置见 04
def embed_texts(texts: list[str], kind: Literal["query","doc"]) -> np.ndarray: ...
def compute_features(profile: ProfileSnapshot, snap: JobSnapshot, parse: JDParse, pref: Preference) -> Features: ...
def rank(method: Literal["b0","b1","b2","b3"], profile: ProfileSnapshot, snaps: list[JobSnapshot],
         pref: Preference, cfg: dict) -> MatchRun: ...
def explain(result: MatchResult) -> Explanation: ...      # 输入结构化特征；引用校验失败→模板解释

# materials/
def map_evidence(parse: JDParse, profile: ProfileSnapshot) -> list[EvidenceLink]: ...
def make_questions(links: list[EvidenceLink], max_n: int = 5) -> list[Question]: ...
def draft_material(kind: str, snap: JobSnapshot, profile: ProfileSnapshot,
                   links: list[EvidenceLink], mode: str) -> Material: ...
def verify_claims(material: Material, profile: ProfileSnapshot) -> list[ClaimCheck]: ...
def approve(material_id: str) -> Material: ...            # 若存在 unsupported/needs_review 抛 ApprovalBlocked
def export(material_id: str, fmt: Literal["docx","pdf","txt"]) -> Path: ...  # 仅 approved

# llm/
def call(purpose: str, template: str, variables: dict, schema: type[BaseModel],
         budget: Budget|None=None) -> LLMResult: ...
```

**调用约束**：`rank` 与 `draft_material` 只接受 `ProfileSnapshot`（含 confirmed 事实）；传入含 candidate 事实的档案是编程错误，须抛异常。

## 5. LLM 输出 schema

### 5.1 简历事实抽取 `FactCandidate`

```json
{ "category": "work|project|education|skill|award|certificate|other",
  "statement": "一句话事实，不含推测",
  "structured": {"org": null, "role": null, "start": "YYYY-MM|YYYY|null", "end": "YYYY-MM|YYYY|至今|null",
                 "skills": [], "metrics": [{"text": "", "value": null, "unit": null}]},
  "contribution": "support|participate|own|push|lead|null",
  "is_estimate": false,
  "source_ref": {"locator": "p1:para3", "excerpt": "原文片段"} }
```

规则：日期保留原精度，不补不存在的起止日；项目成果与个人贡献分开；`contribution` 只在原文有明确动词依据时填写，否则 null；`excerpt` 必须能在原文中逐字找到，否则该候选丢弃并记录。

### 5.2 JD 解析 `JDParse.requirements[]`

```json
{ "id": "r1", "text": "要求原文或紧贴原文的表述",
  "type": "hard|core_skill|responsibility|domain|bonus|other",
  "weight": 1,
  "inferred": false,
  "source_ref": {"span": [120, 168]},
  "normalized": {"skills": ["Python"], "degree": null, "years_min": null} }
```

规则：`weight` ∈ {1,2,3}（3=明确门槛/核心）；`inferred=true`（模型推断的隐含要求）**不得作为硬条件**；“熟悉/优先/加分”不得标 `hard`；`span` 必须落在 JD 原文内。

### 5.3 证据映射 `EvidenceLink`

```json
{ "requirement_id": "r1", "status": "covered|weak|missing|not_applicable",
  "fact_ids": ["..."], "rationale": "≤ 60 字，引用事实而非复述" }
```

规则：`covered` 必须至少一个 confirmed `fact_id`；仅在技能列表出现的技能最多 `weak`；跨事实重复不叠加覆盖；`not_applicable` 需理由。

### 5.4 材料草稿与 Claim

草稿输出为分段结构：`sections[].bullets[].{text, fact_ids[], requirement_ids[], type}`。**每条 `factual` bullet 必须带 `fact_ids`**；缺失则该条视为 `unsupported`。贡献动词不得强于所挂事实的 `contribution` 等级（`support<participate<own<push<lead`）；数字、专名、日期必须与事实 `structured`/`statement` 一致。

### 5.5 校验结果 `ClaimCheck`

```json
{ "claim_id": "...", "check_status": "supported|unsupported|needs_review",
  "reasons": ["number_mismatch|verb_escalation|unknown_skill|date_conflict|org_mismatch|no_fact|semantic_unsupported"],
  "detail": {} }
```

校验分两层：**确定性检查**（数字/专名/日期集合比对、动词等级、事实引用有效、时间线冲突）+ 可选**语义审查**（LLM 辅助，仅能把 `supported` 降级为 `needs_review`，不能把确定性失败升级为通过）。

## 6. 采集字段映射（来自 get_jobs 代码，**`unverified`：以 D1 spike 为准**）

| 内容 | 响应路径（`GET /wapi/zpgeek/job/detail.json`） | 落库字段 |
|---|---|---|
| 岗位名 | `zpData.jobInfo.jobName` | title |
| 薪资文本 | `zpData.jobInfo.salaryDesc` | salary_text（须检查是否含不可读字符，R-01） |
| 地点 | `zpData.jobInfo.locationName` | city |
| 经验 | `zpData.jobInfo.experienceName` | experience_text |
| 学历 | `zpData.jobInfo.degreeName` | degree_text |
| JD 全文 | `zpData.jobInfo.postDescription` | description |
| 岗位加密 ID | `zpData.jobInfo.encryptId` | external_id（与详情页 URL `/job_detail/<id>.html` 的关系需 spike 核对） |
| 公司名 | `zpData.brandComInfo.brandName` | company |
| HR 称谓 | `zpData.bossInfo.title` | hr_title |
| HR 活跃 | `zpData.bossInfo.activeTimeDesc` | hr_active_text |
| （不存） | `zpData.bossInfo.name`、`encryptUserId` | 隐私最小化，丢弃 |

解析失败规则：缺 `jobName` 或 `postDescription` → 该条记 `n_fail`，保留原始 JSON，不入快照；其他字段缺失 → null。

## 7. 错误类型

`ConfigError`（缺配置）· `LLMUnavailable` · `LLMSchemaError`（修复一次后仍失败）· `BudgetExceeded` · `CollectorBlocked`（含 stop_reason）· `ParseError` · `ApprovalBlocked`（有未解决 Claim）· `StaleProfile`（用旧档案版本做新操作）。UI 层统一翻译为中文提示：发生了什么、数据是否保存、下一步。

## 8. 变更传播

- 事实被撤销或修改 → 产生新 profile_snapshot；引用旧快照的 `match_run` 在界面标“旧版”；引用被撤销事实的 `material` 标 `needs_input` 并列出受影响 Claim。已 `approved` 的旧版本保留可查看，不自动改写。
- 偏好改变 → 新 preference revision；旧匹配结果保留并标“旧版”，提供“重新匹配”。
- JD 内容变化 → 新快照；基于旧快照的材料显示“JD 已更新”。
- 清除数据 → 删除 DB 行、`data/raw`、`data/cache`、向量文件、导出文件；清除后校验各目录为空并显示结果。
