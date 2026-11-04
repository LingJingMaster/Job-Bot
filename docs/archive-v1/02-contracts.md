# 数据与接口契约

本文件是跨开发 Agent 的规范性契约。字段增删、枚举变化、授权语义变化须由集成负责人批准并同时更新客户端、迁移和测试。

## 1. 公共数据规范

- ID 使用 UUID；时间使用带时区 RFC3339，数据库保存 UTC，展示默认 Asia/Shanghai。日期不明不得编造具体日。
- 所有私有实体带 `id, owner_id, created_at, updated_at, version`；version 为递增整数。owner_id 从认证上下文取得，不能由请求任意指定。
- 更新使用 `If-Match` 携带当前 version；不匹配返回 409。删除/撤销同样检查版本。不可变快照只允许创建新版本。
- 金额用 decimal 字符串、ISO 币种、周期（月/年/小时）、上下界、是否含浮动部分；缺失用 null。原始薪资文本单独保存。
- 空数组表示确认没有记录；null 表示未知或未提供；不得混用。模型 confidence 仅为提示，不能替代证据或授权。
- 文本、附件及网页内容是不可信输入。所有引用均指向 `source_id + locator + excerpt_hash`，校验归属与来源存在性。
- 字符串长度：标题 200、短备注 2,000、JD/简历纯文本每份 100,000 字符；上传默认 20MB/文件、最多 100 页。超限明确拒绝或要求拆分，不能静默截断事实。

## 2. 核心实体

下表列出公共字段以外的必需字段。`?` 表示可空；标为 JSON 的字段必须有版本化 schema，不能保存无边界任意对象。

| 实体 | 字段 | 约束与关系 |
|---|---|---|
| SourceDocument | kind、storage_key、sha256、mime、size、parse_status、text?、parse_error? | 文件路径由服务端生成；原文不可覆盖；相同 owner+sha256 可复用存储 |
| ProfileVersion | profile_id、revision、facts[]、confirmed_at? | 不可变已确认版本；facts 引用 FactRevision；修改产生新 revision |
| FactRevision | fact_id、revision、category、statement、structured_value、source_refs[]、status、confirmed_by?、confirmed_at? | status=candidate/confirmed/retracted；用户自行陈述可作为显式来源；撤销影响后续生成 |
| PreferenceVersion | strategy_id、revision、hard_constraints[]、soft_weights、search_terms[]、schedule?、budget | 硬条件含 operator/value/unknown_policy；unknown_policy 默认 needs_review |
| PlatformConnection | platform、external_account_ref、credential_ref、status、capabilities、last_checked_at | 凭据仅引用；状态 disconnected/ready/login_required/restricted/error |
| Job | platform、external_job_id?、canonical_url?、dedup_group_id?、latest_snapshot_id | 有平台ID时 owner+platform+external_job_id 唯一；无ID时按来源摘要识别 |
| JobSnapshot | job_id、revision、raw_text、source_ref、title、company、location、salary?、requirements[]、published_at?、fetched_at、availability | 不可变；availability=active/closed/unknown；同 URL 不代表内容不变 |
| MatchRun | profile_version_id、preference_version_id、candidate_snapshot_ids[]、model_manifest_id、config_hash、status | 输入集合固定；重新评分创建新记录 |
| MatchResult | match_run_id、job_snapshot_id、hard_result、score?、rank?、components、evidence_links[]、unknown_fields[] | hard_result=pass/fail/needs_review；fail 不进入默认推荐；score 不是概率 |
| MaterialVersion | material_id、revision、job_snapshot_id、profile_version_id、kind、content、claims[]、status、content_hash、artifact_ids[] | kind=resume/message/cover_letter/interview_pack；status=draft/needs_input/verified/approved/superseded |
| Claim | material_version_id、span、fact_revision_ids[]、jd_requirement_ids[]、check_status | 事实性语句必须有事实支持；纯礼貌语可标 non_factual；check_status=supported/unsupported/needs_review |
| Application | job_id、current_snapshot_id、stage、selected_material_version_id?、last_contact_at?、close_reason? | owner+job_id 唯一；重开复用原申请并保留历史；跨平台疑似同岗由用户确认关联 |
| ExternalAction | application_id?、connection_id、kind、target、payload、payload_hash、status、idempotency_key、approval_id?、attempt_count、evidence_ids[] | kind=greet/send_resume/submit_application/send_message；payload 不可变；每动作有独立结果 |
| Approval | action_id、payload_hash、connection_account_ref、approved_by、approved_at、expires_at、revoked_at? | 默认 30min 有效；单次动作授权；过期必须重新授权；不可迁移给新动作 |
| ActionAttempt | action_id、attempt_no、executor_id、fencing_token、started_at、finished_at?、dispatch_phase、outcome? | dispatch_phase=not_dispatched/dispatching/dispatched；跨边界不明按 unknown |
| Evidence | action_id?、kind、storage_key?、external_ref?、observed_at、summary、trust_level | 截图不必然证明提交；外部记录ID或明确信息优先；避免记录全量私人页面 |
| Conversation | application_id、connection_id?、external_thread_id?、sync_cursor? | 无唯一岗位关联时待用户分配；不得猜错后自动回复 |
| Message | conversation_id、external_message_id?、direction、body、attachments[]、sent_at?、observed_at、source_kind | 来源 platform/manual；有外部ID按线程去重；无ID保留潜在冲突 |
| Interview | application_id、round、starts_at?、timezone、duration_minutes?、location_or_url?、status、material_version_ids[]、notes | status=tentative/confirmed/completed/cancelled；保存改期历史；生成 ICS 不代表对方已接受 |
| OfferVersion | application_id、revision、source_id、terms、deadline?、decision | decision=pending/accepted/declined/expired；本地记录决定，不隐含外部发送 |
| Feedback | target_type、target_id、kind、reason_codes[]、free_text?、context_version | kind=save/reject/correct/rating；用户未操作不是负标签 |
| Task | kind、input_refs、status、step、checkpoint、next_run_at、lease_owner?、lease_until?、fencing_token、attempt、budget、error? | 工作流事实源；不把敏感正文放通用日志 |
| Schedule | strategy_id、timezone、rule、next_run_at、enabled、last_occurrence_key | occurrence_key 唯一；睡眠补跑一次；不重放每个错过时间点 |
| Notification | type、target_ref、dedup_key、due_at、read_at?、status | owner+dedup_key 唯一；取消面试取消对应未来通知 |
| DomainEvent | aggregate_type、aggregate_id、aggregate_version、type、payload、sequence、task_id? | 同事务写入 outbox；对外展示需脱敏和 owner 过滤 |
| ModelManifest | model_id、revision、provider、tokenizer_revision?、embedding_dim?、prompt_hash?、config、license_ref | 模型、提示词或向量维度改变必须新版本 |
| ChatSession | title、context_refs[]、archived_at? | 用户与Job-Bot的对话，区别于招聘平台Conversation |
| ChatMessage | session_id、role、content、attachment_refs[]、task_refs[]、client_message_id | role=user/assistant；owner+session+client_message_id唯一；工具事件单独记录 |
| TaskPlan | session_id?、revision、intent、steps[]、input_refs、status | status=proposed/accepted/running/completed/needs_revision；有限白名单步骤，不保存任意可执行程序 |
| ExclusionRule | target_type、target_ref、display_name、scope、reason、expires_at?、enabled | target_type=company/job/contact；scope=search/send/both；软删除保留历史 |

必须有索引：各表 owner_id；岗位 owner+platform+external_job_id；申请 owner+job_id；事件 owner+sequence；Task(status,next_run_at/lease_until)；动作 owner+idempotency_key；消息 conversation+external_message_id。向量按模型版本和维度分区或独立表，绝不混合比较。

## 3. 三套状态机

### 3.1 申请业务阶段

`interested → preparing → ready → submitted → communicating → interviewing → offered`

- interested：保存或创建申请；preparing：已开始材料；ready：所选材料批准且关键条件已处理。
- submitted：明确提交证明，或用户回填“已申请”；必须记录 origin=platform/manual 和 evidence。
- communicating：存在招聘方回复或用户回填；单方面打招呼不自动证明对方已回复。
- interviewing：至少一次已确认面试；offered：有 Offer 记录。
- 任一非终态可转 rejected/withdrawn/closed，须原因和来源。offered 可关闭并记录接受结果。
- 允许用户纠正/重开及补录跳阶段，通过 `stage-corrections` 事件写入原因，不通过任意 PATCH 绕过规则。
- 动作失败不把整个申请标为 rejected；材料尚未完成时可接收外部消息，阶段与任务状态独立。

### 3.2 外部动作

`pending_approval → queued → running → succeeded | failed | unknown`

- pending_approval 可转 cancelled/expired；审批成功并事务入队后 queued。
- queued 可转 cancelled/expired；运行前再次检查有效期、内容摘要、账户和能力。
- running 只有明确证明尚未执行副作用时可安全取消；缺少证据不等于尚未发送。已经开始提交或边界不明时不能声称撤回，需核实。
- failed 只用于确定未发生目标副作用。重试保留同 action、增加 attempt，并重新检查授权有效性。
- unknown 禁止进入自动重试队列。只允许 reconciliation 核实为 succeeded，或有明确未发生证据后标 failed；无法核实保持 unknown。用户手动补录也必须保留“用户声明”的证据等级。
- 已成功动作不能重试；再次发送必须创建新动作、展示历史并取得新授权。提交申请的重复业务检查仍阻止普通重复操作。

### 3.3 后台任务

`queued → running → succeeded | failed | waiting_user | paused | cancelled`

waiting_user 保存问题/登录/审批引用，不占 Worker；用户回答后恢复到 queued；paused 由用户或预算/限制触发。租约过期的纯计算任务可重新入队；含已 dispatch 的动作转 unknown 并进入核实任务，不重复执行。

## 4. 外部动作的一致性协议

1. prepare 根据不可变材料、岗位快照、账户和目标生成 payload，计算 SHA-256。规范化 JSON 必须固定键顺序、编码及附件摘要；审批预览展示的正文与执行正文完全一致。
2. approve 校验 owner、version、hash、有效期，在同一数据库事务写 Approval、queued 状态与 outbox。客户端重复提交相同 Idempotency-Key 返回原结果；同 key 不同正文返回 409。
3. Worker 领取任务时使用短事务行锁与租约，不在数据库事务中等待浏览器或模型。领取成功增加 fencing_token。
4. 执行器按 connection 串行，验证 fencing_token、授权与前置状态，记录 ActionAttempt 后才发送。过期 Worker 的回写被拒绝。
5. 外部发送不是数据库事务的一部分。发送后进程崩溃、超时或结果无法解析都进入 unknown。租约到期不能启动第二个发送者；必须先确认旧执行器停止并核实平台结果。
6. 结果和证据在同一事务记录，满足申请完成条件才推进 Application；事件采用 outbox 至少一次投递，消费者按 event_id 去重。
7. 撤销授权与 dispatch 竞争时，以执行器原子取得 dispatch 许可为界：许可前撤销成功；许可后返回“正在核实，无法保证停止”。不把 UI 点取消视为外部动作已撤回。

系统不宣称端到端 exactly-once。内部靠唯一约束和幂等键避免重复调度，外部靠单执行器、动作记录和结果核实减少重复副作用。

默认运行参数：任务租约60s、心跳15s；单次页面操作超时30s；只读网络错误最多重试2次，退避2s/8s加抖动；外部写动作默认不做通用重试。浏览器执行中的动作心跳丢失即停止领取同账户后续动作，进入执行器健康核查；上述时间到期仅说明失联，不证明外部动作未发生。授权有效期默认30min、上限24h，任务预算与平台限制可设置更短期限。

## 5. HTTP 通用约定

前缀 `/api/v1`；JSON UTF-8；成功对象 `{data, request_id}`，集合 `{data, next_cursor, request_id}`；异步操作 202 返回 `{task_id,status,events_url}`。错误 `{error:{code,message,retryable,details},request_id}`。日志保存 request_id，不将完整简历写入 details。

分页 limit 默认 20、上限 100，cursor 不透明并绑定排序与过滤条件。排序加 ID 作为稳定次键。创建/动作接口接受 Idempotency-Key；作用域 owner+route+key，保留至少 7 天；动作自身防重记录保留至申请删除。读取没有副作用。

状态码：400 非法请求；401 未认证；403 权限不足；404 不存在或非本人对象；409 版本/幂等/状态冲突；413 文件超限；422 字段或业务校验失败；429 限流；503 依赖不可用。工具错误码另列，不向前端泄露凭据。

## 6. API 清单

表中 JSON 字段引用实体定义；列表 GET 支持上述分页。所有 id 查询必须检查 owner。只有标为“外部动作”的端点会进入发送流程。

| 方法与路径 | 请求核心字段 | 响应与语义 |
|---|---|---|
| POST /documents | multipart file、kind | 201 SourceDocument；异步解析 task_id |
| GET /documents/{id} | — | 解析状态、文本引用，不回传服务器路径 |
| POST /profiles | name | 201 档案标识与初始草稿 |
| POST /profiles/{id}/versions | facts、base_revision | 201 ProfileVersion；候选事实不能隐式确认 |
| POST /facts/{id}/confirm | revision、source_refs、confirmation | 201 确认后的 FactRevision |
| POST /facts/{id}/retract | revision、reason | 新修订，相关材料标需复核 |
| GET /profiles/{id} | revision? | 当前或指定档案版本 |
| POST /strategies | name、hard_constraints、soft_weights、search_terms、budget | 201 初始 PreferenceVersion |
| POST /strategies/{id}/versions | base_revision、上述字段 | 201 新版策略 |
| POST /search-runs | strategy_version_id、connection_ids、limit | 202 搜索 Task |
| POST /jobs/import | text 或 allowed_url 或 document_id、source_kind | 202 导入 Task；不抓任意内网 URL |
| GET /jobs | filters、sort、cursor | 岗位摘要、最新快照与来源 |
| GET /jobs/{id} | snapshot_id? | 详细快照与变更提示 |
| POST /jobs/{id}/refresh | connection_id | 202 刷新 Task，不覆盖旧快照 |
| POST /match-runs | profile_version_id、strategy_version_id、job_snapshot_ids | 202；GET /match-runs/{id} 获取结果 |
| POST /feedback | target_type、target_id、kind、reason_codes | 201 Feedback |
| POST /applications | job_id、snapshot_id | 201 Application；重复返回已有申请或409 |
| GET /applications | stage、cursor | 看板列表 |
| GET /applications/{id} | — | 申请及时间线引用 |
| POST /applications/{id}/stage-corrections | from、to、reason、origin、evidence_ids | 版本检查后记录更正，绝不自动外发 |
| POST /materials/generate | application_id、profile_version_id、job_snapshot_id、kind | 202；返回追问或草稿 |
| GET /materials/{id}/versions/{revision} | — | 内容、claims、校验与来源 |
| POST /materials/{id}/versions | base_revision、content、claim_links | 201 新草稿，修改后重跑校验 |
| POST /materials/{id}/verify | revision | 202 校验 Task |
| POST /materials/{id}/approve | revision、content_hash | 201批准记录；unsupported claim 阻止批准 |
| POST /materials/{id}/export | revision、format=pdf/docx/txt | 202；GET /artifacts/{id}/download 按归属下载 |
| POST /actions/prepare | application_id、connection_id、kind、target、material_version_ids、body | 201 ExternalAction 与固定预览；没有发送 |
| POST /actions/{id}/approve | payload_hash、expires_in_seconds | 202 入队；仅用户认证通道可调用 |
| POST /actions/{id}/cancel | reason | 状态/dispatch 边界检查后的结果 |
| POST /actions/{id}/reconcile | evidence_ids?、manual_observation? | 202 核实；不执行发送 |
| GET /actions/{id} | — | 动作、attempt、证据与待办 |
| POST /connections | platform、local_profile_ref | 建立连接元数据，无密码入请求 |
| POST /connections/{id}/check | — | 202 登录与能力检查 |
| DELETE /connections/{id} | version | 停任务、撤销凭据引用；不删除申请历史 |
| GET /connections | — | 能力矩阵与检查时间 |
| POST /conversations/import | application_id、messages、source_kind | 去重导入；不发送消息 |
| POST /conversations/{id}/sync | connection_id | 202 能力支持时增量同步 |
| GET /conversations/{id}/messages | cursor | 消息页 |
| POST /conversations/{id}/draft-reply | message_ids、instruction? | 202 MaterialVersion 草稿；发送仍走 actions |
| POST /interviews | application_id、time_fields、round、source_id? | 201 tentative 或 confirmed |
| PATCH /interviews/{id} | changed_fields、reason | 时区/冲突提示，新修订 |
| POST /interviews/{id}/preparation | material_version_ids | 202 面试准备包 |
| POST /interviews/{id}/review | outcome、notes、feedback | 面试复盘，不自动修改事实底稿 |
| GET /interviews/{id}/calendar | — | ICS 下载，无外部日历写入 |
| POST /offers | application_id、source_id、terms | 201 OfferVersion |
| POST /offers/{id}/versions | base_revision、terms、decision | 修订与本地决定记录 |
| POST /offers/compare | offer_version_ids、weights | 条款比较、未知项和计算说明 |
| GET /analytics | cohort、date_range、strategy_id? | 数量、分母、观察窗口、指标 |
| POST /schedules | strategy_id、rule、timezone、budget | 201 读取任务计划 |
| PATCH /schedules/{id} | enabled、rule? | 更新后重算 next_run_at |
| GET /notifications | unread、cursor | 通知列表；POST /notifications/{id}/read 标已读 |
| GET /tasks/{id} | — | 状态、步骤、checkpoint 摘要 |
| POST /tasks/{id}/control | command=pause/resume/cancel、answer? | 检查等待类型与状态转换 |
| GET /events | after、task_id? | SSE 事件流，Last-Event-ID 恢复 |
| POST /data-exports | scope、include_originals | 202 可下载脱敏清单和业务文件 |
| POST /data-deletions | scope、target_ids、confirmation | 202 删除任务；阻止相关动作再执行 |

全量列表端点还包括 GET /profiles、/strategies、/materials、/interviews、/offers、/schedules；响应遵循统一分页。不开放任意“执行脚本”“调用工具名”HTTP 端点。

### 示例交互

创建动作的请求包含 `application_id=…、kind=send_resume、connection_id=…、material_version_ids=[…]`，响应给出 `action_id、status=pending_approval、payload_hash、preview`。用户确认后调用 approve，携带相同 payload_hash。若用户在另一窗口更换材料，应创建新动作；旧动作不能被悄悄改写。重复 approve 同 key 只能返回原任务，不生成第二次发送。

### 对话与排除名单接口

| 方法与路径 | 请求 | 响应与规则 |
|---|---|---|
| POST /chat-sessions | title? | 201 ChatSession |
| GET /chat-sessions | cursor | 本人会话列表 |
| GET /chat-sessions/{id}/messages | cursor | 消息与实体/任务引用 |
| POST /chat-sessions/{id}/messages | client_message_id、content、attachment_refs、context_refs | 202 编排Task；重复消息不创建重复计划 |
| GET /plans/{id} | — | 固定步骤、待确认项与预算 |
| POST /plans/{id}/accept | revision、selected_options | 202执行只读/分析步骤；不包含外部动作授权 |
| POST /plans/{id}/revise | revision、changes | 新计划版本；已执行步骤保留，不回滚外部结果 |
| POST /exclusions | target_type、target_ref、scope、reason、expires_at? | 201 ExclusionRule |
| GET /exclusions | enabled、cursor | 规则列表 |
| PATCH /exclusions/{id} | enabled、scope?、expires_at? | 版本检查更新，无外部副作用 |

新增事件 chat.message.completed、plan.proposed、plan.waiting、exclusion.updated。对话的结构化卡片使用明确类型和entity_ref，前端从业务API重新读取关键内容；不能直接执行模型返回的HTML或按钮URL。

### 变更传播与删除

撤销事实、更新选定材料、刷新到内容改变的JD、断开账户、启用发送排除规则时，业务事务将相关pending_approval/queued动作置expired或cancelled并写事件。Worker在dispatch前仍需读取最新依赖状态，防止传播延迟。已running动作按dispatch边界处理；已succeeded动作保留历史，仅提示旧材料可能需要后续澄清，不篡改过去结果。

删除任务先将目标标为deleting并撤销未dispatch动作；正在外发的动作必须结束或核实后再删除执行相关数据。随后按依赖删除向量、索引、派生文件、原件及业务对象，记录不含个人正文的删除完成证明。引用计数非零的共享文件不提前移除；同owner导出/备份残留按保留政策处理。

## 7. SSE 与事件

事件信封为 `event_id, sequence, type, occurred_at, aggregate_type, aggregate_id, aggregate_version, task_id, data`。类型至少包括 task.progress/task.waiting/task.completed/task.failed、job.discovered、match.completed、material.needs_input/material.verified、action.approval_required/action.unknown/action.succeeded、message.received、interview.updated、notification.created。

sequence 是 owner 范围单调递增。客户端按 event_id 去重，收到旧 aggregate_version 不回退 UI；断流从 Last-Event-ID 补齐。事件保留默认 30 天；游标已失效返回需要全量刷新标记，客户端重新获取实体快照。心跳每 15s，断连不取消业务任务。

## 8. 平台适配与 MCP

适配器接口定义为能力函数；HTTP/MCP 都只能进入同一应用策略服务：

| 方法 | 输入 | 输出 |
|---|---|---|
| capabilities | connection_ref | search_jobs/read_job/greet/send_resume/submit_application/read_messages/send_message/reconcile 各项状态与最近验证时间 |
| check_session | connection_ref | ready/login_required/restricted/error 与原因 |
| search_jobs | normalized_query、cursor、limit | jobs[]、next_cursor、observed_at、partial、warnings |
| read_job | external_job_ref | raw_text、normalized_fields、source_locator、observed_at、availability |
| execute_action | action_id、approved_payload、dispatch_permit、fencing_token | outcome、external_ref?、evidence[]、error? |
| reconcile_action | action_id、target、time_window | succeeded/definitely_not_sent/inconclusive 与证据 |
| read_messages | thread_ref、cursor、limit | messages[]、next_cursor、has_more、observed_at |

能力状态为 unsupported/unverified/verified/degraded。初始真实平台全部 unverified；测试通过指定能力后才设 verified，不能因为 README 声称支持而打开按钮。

统一工具错误码：LOGIN_REQUIRED、HUMAN_VERIFICATION_REQUIRED、PLATFORM_RESTRICTED、SELECTOR_CHANGED、JOB_CLOSED、CAPABILITY_UNSUPPORTED、RATE_LIMITED、NETWORK_TIMEOUT、RESULT_UNKNOWN、CONTENT_CHANGED、APPROVAL_EXPIRED。每个错误含 safe_to_retry、side_effect_possible 和 user_action；外部副作用可能发生时 safe_to_retry 必须为 false。

MCP 初始仅提供岗位查询、档案只读摘要、匹配、材料草拟、任务查询和 prepare_action。不得暴露 approve 或无策略约束的底层发送。MCP 使用者的身份必须绑定 owner，工具描述不能扩大权限。
