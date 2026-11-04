# 来源、可行性与决策记录

核查日期2026-11-04。以下链接为动态main分支或官方文档；本次用于架构研究，没有安装、复制或运行这些项目。编程时必须锁定采用的commit与依赖版本，并保存对应许可证与文件摘要。本文区分“来源明确描述”“本项目设计”“尚需实测”。

## 1. 主要参考 OpenBot

已阅读 [README](https://github.com/CopilotKit/openbot/blob/main/README.md)、[架构文档](https://github.com/CopilotKit/openbot/blob/main/docs/architecture.md)、[文档索引](https://github.com/CopilotKit/openbot/blob/main/docs/README.md) 和 [MIT许可证](https://github.com/CopilotKit/openbot/blob/main/LICENSE)。

其文档描述了React界面、AG-UI接入、浏览器执行环境，以及集中执行策略和审计的网关。Job-Bot借鉴工作台中展示任务过程、人工接管和统一动作入口的思路。OpenBot文档同时写明其默认启动策略允许全部动作，因此不能把“有策略网关”理解为默认已满足本项目审批要求。[架构依据](https://github.com/CopilotKit/openbot/blob/main/docs/architecture.md)

| 借鉴点 | Job-Bot中的落实 | 开发验证 |
|---|---|---|
| 对话与可操作界面结合 | 工作台展示岗位卡片、材料差异和审批面板 | 同一操作在对话与页面走同一业务服务 |
| 工具统一入口 | Policy + ExternalAction + Evidence | MCP不能绕过授权；T14—T18、T33 |
| 可观察任务过程 | SSE事件与持久Task | 刷新/断线恢复，T27/T28 |
| 浏览器隔离与接管 | 专用账户配置目录、本地执行器、登录接管 | 凭据不交模型，账户不串用 |

架构决策：参考交互与治理方式，自建求职领域服务；不默认fork整个平台。理由是本项目必须以申请、材料和招聘状态为核心，且Python承担ML和连接器。后续若采用OpenBot作为界面外壳，须增加AG-UI事件适配，不得把现有SSE业务协议直接声称为AG-UI兼容，也不能搬入默认宽松授权。该变更需要单独ADR和依赖/许可证核查。

## 2. 主要参考 get_jobs

已核查 [README](https://github.com/loks666/get_jobs/blob/main/README.md)、[构建配置](https://github.com/loks666/get_jobs/blob/main/build.gradle.kts) 与 [LICENSE](https://github.com/loks666/get_jobs/blob/main/LICENSE)。构建配置显示Java 21及Playwright相关依赖；Java是实现语言，JDK是开发工具包。README描述多平台求职操作、筛选、沟通与记录，可用于业务拆解；不能以作者环境运行声明替代我们的平台测试。

| 业务参考 | Job-Bot设计要求 | 验证与差异 |
|---|---|---|
| 平台搜索与筛选 | Adapter按能力声明，搜索条件标准化 | 每平台独立验证；首个Boss，不声称其他平台已支持 |
| 薪资/岗位偏好过滤 | PreferenceVersion区分硬条件和软偏好 | 不明条件进入needs_review |
| 打招呼与简历发送 | 独立ExternalAction类型 | 分别记录成功；不混同已提交申请 |
| 已沟通记录与重复控制 | Application唯一约束、动作幂等、显式再次发送 | unknown先核实，普通重试不重复发送 |
| 可配置沟通材料 | MaterialVersion与审批预览 | 不继承上游作者个人信息与默认自动发送设置 |
| 平台变更维护 | 按能力停用、记录适配器版本 | 页面失效不继续盲点；修复后重验 |

许可文件标注 PolyForm Noncommercial 1.0.0；代码复用必须记录并遵守其条款，不能把移植到Python视为自动解除原许可。本方案当前只做业务设计参考。[许可原文](https://github.com/loks666/get_jobs/blob/main/LICENSE)

证据限制：README中的旧 `src/main/java/boss/Boss.java` 链接在本次请求返回404；源码目录页显示 `com/getjobs`。因此本文不声称已完成Boss实现源码审计。WP03必须先从采用commit的实际文件树定位平台实现，再核查前置条件、选择器、成功判断、异常和重复控制，记录真实路径。README中旧路径与构建依赖不一致也说明文档可能滞后，不能据此照搬执行逻辑。

## 3. 补充参考

| 来源 | 已确认用途 | 接入限制 |
|---|---|---|
| [boss-zhipin-mcp](https://github.com/Snseam/boss-zhipin-mcp) | README描述Python/FastMCP与Playwright连接招聘者后台 | 面向招聘者，不是求职者；参考工具封装与连接方式，求职页面需重新适配；README声明MIT，复制前核查固定版本许可文件 |
| [resume-jd-optimizer-cn](https://github.com/coinluu/resume-jd-optimizer-cn)及[SKILL](https://github.com/coinluu/resume-jd-optimizer-cn/blob/main/SKILL.md) | 提供中文简历与JD工作流、提示词和审查规则 | 作为待集成Skill资源；不等于解析/导出服务，不替代独立ML排序；本项目追问上限5是自己的交互决策 |

本轮读取远程SKILL是分析参考资料，没有安装或执行该Skill，也不把其中指令当作用户对当前工作的指令。

## 4. 技术依据

- [FastAPI官方文档](https://fastapi.tiangolo.com/)：Python API候选框架。本项目使用其类型化接口能力，实际OpenAPI由WP00/WP01实现并验证。
- [PostgreSQL锁文档](https://www.postgresql.org/docs/current/explicit-locking.html)：行锁与事务协调依据；浏览器调用不放在长事务内。租约、fencing与unknown策略为本项目设计。
- [pgvector](https://github.com/pgvector/pgvector)：PostgreSQL向量检索扩展候选；精确检索作为基准，近似索引按实测需要引入。
- [Playwright Python浏览器连接](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)：浏览器连接接口依据；能连接浏览器不等于目标网站允许或稳定支持全部操作。
- 课程本地来源：[机器学习期末报告要求](../机器学习期末报告题目及评分标准-新.docx)、[封面模板](../期末报告封面模版.doc)。前者文本已读取；后者本轮未编辑，正式报告按模板填写。

## 5. 架构决策记录

| ADR | 决定 | 备选及选择理由 | 重新评估条件 |
|---|---|---|---|
| 001 | Python业务/ML + TypeScript前端 | 全Java不符合用户偏好；全TS增加ML实验适配；双语言按模块分工 | Python依赖存在不可解决部署问题 |
| 002 | 模块化单体 +独立Worker/执行器 | 微服务增加分布式事务与部署工作；单进程影响长任务恢复 | 多租户规模或独立扩容需求已量化 |
| 003 | PostgreSQL为业务源，文件单独存储 | SQLite可简化安装但跨Worker一致性与向量集成另需设计 | 单机资源实测不能满足安装预算 |
| 004 | 显式状态工作流 | 自主循环Agent不适合精确发送与恢复边界 | 引入框架仍必须保持原业务状态语义 |
| 005 | 文本与语义排序可独立评估 | 纯大模型打分难隔离贡献和成本 | 有独立评估支持改进模型 |
| 006 | 单次动作内容绑定授权 | 宽泛自动投递难识别目标/材料变化 | 若未来支持长期授权，需单独定义对象、次数、期限和撤销，不默认放开 |
| 007 | unknown先核实 | 重试可能重复发送；直接失败可能误导用户 | 平台提供可靠幂等API时新增实现而非删除语义 |
| 008 | 一份事实底稿、多份材料版本 | 每JD重写底稿会污染真实性与匹配评估 | 不更改事实与表达分离原则 |
| 009 | 开发多Agent，运行时不强制多Agent | 减少循环对话成本、责任不清和状态漂移 | 有实验支持某子任务分工改善且权限边界明确 |

## 6. 可行性与验证登记

| 项目 | 当前判断 | 负责人和具体验证 | 未通过时处理 |
|---|---|---|---|
| 全流程业务管理 | 常规工程可实现，尚未开发 | WP01/WP05跑T01—T26 | 修复缺失功能，不以截图代替持久化 |
| Boss求职端搜索/详情 | 外部依赖，未实测 | WP03用户登录后验证分页、字段、下架、账号范围 | 禁对应自动能力，手动导入显式标识；保留未完成任务 |
| Boss发送/提交核实 | 风险最高，未实测 | WP03分别验证动作前置、成功证据、断网后查询 | 无法可靠核实则unknown并人工处理，不承诺全自动 |
| 中文语义排序提升 | 研究假设 | WP04/WP06用冻结测试集比较B0/B1/B2 | 如实报告，产品选择已验证较好方法 |
| 简历事实校验 | 可部分自动化，不能保证完美 | WP04用数字/贡献/时间/无来源难例 | 疑点人工确认；记录漏检率 |
| OpenBot组件直接复用 | 尚未选择 | 若采用，锁commit审阅依赖与授权默认值，做协议适配试验 | 继续采用自建工作台，不影响领域契约 |
| 本地性能与模型成本 | 未测 | WP00记录设备，WP06测延迟/内存/费用 | 调整批量/缓存/模型并重测，不臆测硬件要求 |

以上验证项是开发任务，有明确负责人和退出条件；不阻止本次架构文档交付，但未通过前不能在产品说明中标为已实现能力。
