# 来源、已核验事实、决策与未验证项

核查日期：2026-11-04。区分三类陈述：**已核验**（本轮实际读取了文件/代码）、**设计参考**（来自 v1 文档或 README 描述，本轮未重新核实）、**未验证**（需实施阶段取得运行证据）。

## 1. 复用与参考组件清单

| 组件 | 版本/commit | 许可证 | 用途 | 状态 |
|---|---|---|---|---|
| [loks666/get_jobs](https://github.com/loks666/get_jobs) | `36bf5f986fad93374d77b99205c7684f8450874a`（2026-11-04 克隆的 main 头） | PolyForm Noncommercial 1.0.0（`LICENSE` 已读） | **设计参考**：采集思路（监听详情接口）、字段映射、DOM 选择器起点。**不复制代码**；本项目用 Python 重写 | 已核验代码；运行结果 `unverified` |
| [coinluu/resume-jd-optimizer-cn](https://github.com/coinluu/resume-jd-optimizer-cn) | `114c85c32dc77bc9ec7315976a21a1f761868664` | MIT（`LICENSE` 已读） | **提示词与真实性规则的起点**：`prompts/{jd_parser,gap_analyzer,experience_interviewer,resume_rewriter,risk_checker}.md`、`docs/resume_truthfulness_policy.md`；改写为 JSON schema 输出并加校验 | 已核验结构与规则；行为需 E3 验证 |
| [CopilotKit/openbot](https://github.com/CopilotKit/openbot) | 未锁定 | MIT（v1 文档记载） | **仅设计参考**：对话与可操作界面结合、任务过程可观察、统一动作入口。**未使用其代码** | 设计参考，本轮未重新核实 |
| [Snseam/boss-zhipin-mcp](https://github.com/Snseam/boss-zhipin-mcp) | 未锁定 | README 声明 MIT（v1 记载） | 面向招聘者端，**不使用**；仅知悉其 Playwright + MCP 封装方式 | 不采用 |
| [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | WP-D 锁定 revision | 模型卡标注 MIT（v1 记载，锁定时核对） | B1 embedding | `unverified`（中文质量需 E1 验证） |
| scikit-learn | 锁定版本 | BSD-3-Clause（锁定时核对） | TF-IDF、逻辑回归、指标 | 待锁定 |
| Playwright for Python | 锁定版本 | Apache-2.0（锁定时核对） | 采集器浏览器控制 | 待锁定 |
| Streamlit、python-docx、PDF 导出库、pytest、pydantic、numpy | 锁定版本 | 逐项核对 | 界面、导出、测试、校验 | 待锁定 |
| 中文 PDF 字体 | WP-E 选定 | 须允许嵌入与分发 | 导出 | 待选定 |

**所有项在最终 `THIRD_PARTY.md` 中补全**：名称、版本/commit、来源链接、许可证文件摘要、用途、复制/修改的文件、原创部分、核验日期与结果。只参考思路的必须写“设计参考，未复制代码”。get_jobs 的非商业许可与 MIT 组件分别列出，不得统一写“全部 MIT”。

## 2. 已核验的组件事实（报告“深入分析一个 skill”的素材）

### 2.1 get_jobs 的 Boss 采集（commit `36bf5f9`）

- 实现位于 `src/main/java/com/getjobs/worker/boss/`：`Boss.java`（1858 行）、`Locators.java`、`BossConfig.java`、`BossChatNavigator.java`。其 README 仍链接旧路径 `src/main/java/boss/Boss.java`，该路径在此 commit 中不存在（已核验），以上述实际路径为准。
- **采集机制**：`Boss.attachJobDetailResponseListener()` 对页面注册 `onResponse`，筛选 URL 含 `/wapi/zpgeek/job/detail.json` 的 GET 响应，读取 JSON；`zpData.jobInfo`（`jobName`、`salaryDesc`、`locationName`、`experienceName`、`degreeName`、`postDescription`、`encryptId`、`encryptUserId`）、`bossInfo`（`name`、`activeTimeDesc`、`title`）、`brandComInfo`（`brandName`）被用于过滤与入库。点击卡片处用 `page.waitForResponse` 等待同一接口。
- **DOM 选择器**（`Locators.java`）：列表卡 `ul.rec-job-list li.job-card-box`、`a.job-name`、`span.boss-name`、`span.company-location`、`ul.tag-list li`；详情页 `//div[@class='job-sec-text']`；其代码注释说明首页搜索走真实 UI 输入（`input.ipt-search` / `button.btn-search`，逐字符输入延迟 140ms）而不是拼 URL，目的是降低风控概率。
- **写操作**：同一模块包含点击沟通按钮（`a.btn-startchat`）、输入并发送消息、发送图片简历的代码。**本项目只借鉴读取部分，不实现任何写操作**，由 T07 约束。
- **限制与风险**：Java 21 + Playwright；选择器依赖当前页面结构；README 链接已过时，说明文档可能滞后；运行结果取决于作者环境，不能代替我们的验证。
- **验证方法（WP-A）**：用 `scripts/spike_boss.py` 观察实际响应；对照上表字段；记录差异。

### 2.2 resume-jd-optimizer-cn（commit `114c85c`）

- 形态：Agent Skill（`SKILL.md` + `prompts/` + `rubrics/` + `docs/` + `examples/` + `tests/`）；约 70 个受控文件，MIT。
- 流程：8 步（收集输入→JD 解析→简历解析→缺口分析→素材追问→重写→ATS/HR/面试官检查→最终报告）；场景 A–E 的“阶段响应契约”（如只有 JD 时禁止输出匹配结论、造假请求时停止评分）。
- 真实性规则（可直接作为本项目校验器规则的依据）：三态标记（已确认/待确认/模型推断，只有已确认可入终稿）；动词证据阶梯“支持/协助 < 参与 < 负责 < 推动 < 主导，以最低已确认证据为准”；数字必须有来源或估算标记；“包装”不得升级职责、职级或结果。
- 局限：输出为长文本，无结构化 schema、无持久化、无自动校验；评分表由 LLM 打分，无标注依据，分数不是录用概率；`tests/` 是 Markdown 用例清单而非可执行测试。
- **本项目的适配与核验**：见 04 §4；E3 对照“使用其提示词/不使用”；E5 检验“规则是否真的挡住伪造陈述”——**规则文字要求不编造 ≠ 模型不会编造**，必须以实验证明。

## 3. 架构决策记录（ADR，v2）

| ADR | 决定 | 备选与理由 | 重新评估条件 |
|---|---|---|---|
| 001 | Streamlit 直连服务层 | React+FastAPI 更像产品但多 2–3 天；单人两周不值 | 需要更复杂交互或多人使用 |
| 002 | SQLite + numpy，无 PostgreSQL/pgvector | v1 理由“跨 Worker 一致性”在单进程下不成立；评阅者复现更容易；规模 ≤ 数千岗位精确检索足够 | 岗位量 > 1e5 或多用户 |
| 003 | 采集只读，无任何发送/投递代码 | 自动投递对课程评分无直接收益且风险最大 | 另立项目并重新设计审批协议（见 archive-v1） |
| 004 | 监听详情接口响应（模式 A 为主） | 纯 DOM 抓取对改版更脆弱；直接构造请求需破解参数，不做 | spike 证明接口不可用 → 转 manual_import |
| 005 | 不实现 v1 的审批/ExternalAction/unknown 状态机 | 无外部写动作则无需；省出的时间用于 ML 与实验 | 引入任何外部写动作时必须先恢复 v1 协议 |
| 006 | B3（训练）列为强烈建议保留的 P1 | B0–B2 无训练，课程“训练/评估”证据薄弱 | 标注总量 < 150 → 降级为方向性实验 |
| 007 | 池化标注 + LOPO + job-group 折，不用 60/20/20 | 样本少，固定小测试集方差过大 | 获得更多 profile |
| 008 | 校验器单列为分类实验 E5 | 低成本、可定量、直接服务“真实性”卖点 | — |
| 009 | LLM 磁盘缓存与 replay 模式 | 保证无 Key 复现与实验可重算 | — |
| 010 | 单用户，无 owner_id | 个人本地工具；减少无谓复杂度 | 多用户托管时统一加入并重做权限检查 |
| 011 | 保留“事实底稿 vs 材料版本”分离 | 防止每个 JD 改写污染事实；这是项目核心卖点 | 不变更 |
| 012 | LLM 用 OpenAI 兼容接口 | 可换提供方，成本可控 | 具体模型由项目负责人在 WP-B 前确定并记录 |

## 4. 未验证项与风险登记

| ID | 项目 | 判断 | 验证（负责） | 未通过时 |
|---|---|---|---|---|
| R-01 | Boss 详情接口当前可用，且 `salaryDesc` 为可读文本（历史上站点曾用自定义字体混淆页面数字） | `unverified` | WP-A D1 spike | 薪资当 null，硬条件按 `needs_review`；记入局限 |
| R-02 | 点击岗位卡片触发详情请求；模式 B 的选择器仍有效 | `unverified` | WP-A | 只做模式 A |
| R-03 | 采集速率下不触发验证/限制 | `unverified` | E0 | 降速、降上限；触发即停并如实报告 |
| R-04 | `bge-small-zh-v1.5` 在“档案 vs JD”任务上优于 TF-IDF | 研究假设 | E1 | 如实报告；产品选用已验证较优者 |
| R-05 | 校验器能挡住自然产生的夸大 | 部分可自动化，不保证 | E5、E3 审计 | 疑点人工确认；报告漏检率 |
| R-06 | 同学简历与标注能按时取得 | 依赖他人 | WP-F D4 | 降为单 profile 并明示泛化限制 |
| R-07 | LLM 提供方与预算 | 未定 | 项目负责人在 D1 前确定 | 无 Key 时走降级与 replay |
| R-08 | 平台条款与账号风险 | 已知悉并接受 | 报告披露与缓解 | — |
| R-09 | 本机资源能运行 embedding 与导出库 | 未测 | WP-B/WP-D 记录机器规格 | 换更小模型并重跑验证 |
| R-10 | 2 周排期过紧 | 高 | 每日按证据更新 | 按 05 §1 砍项 |

## 5. v1 → v2 变更摘要

| v1 内容 | v2 处理 |
|---|---|
| F01–F14 | 保留 R01–R07 的精简版；F07–F11、F13、F14 移出范围 |
| PostgreSQL + pgvector + Worker + Redis 替代方案 | SQLite + numpy + 后台线程（ADR-002） |
| 审批、ExternalAction、ActionAttempt、fencing token、outbox、SSE | 删除（ADR-005）；无外部写动作 |
| WP00–WP07 与多 Agent 开发 | 重编为 WP-A…WP-H，面向单人负责 + 协助者 |
| 40 个验收场景 | 压缩为 23 个，覆盖保留的需求 |
| 30 档案 / 300 岗位 / 60-20-20 划分 | 1–5 档案 / 200–300 岗位 / 池化 + LOPO（ADR-007） |
| E01–E04 | E0–E5（新增采集可靠性、抽取质量、校验器分类） |
| 来源链接与许可证核查 | 补做 get_jobs 与 resume-jd-optimizer-cn 的实际核验，修正 v1 中过时路径 |
| v1 `README` 中“不把截止时间当作删除功能的理由” | 废止；范围由项目负责人决定，本版已决定（见 README） |
