# 架构与实现约束

## 1. 总体方案

单机、单用户、模块化单体。一个仓库、一个 Python 包、一个 SQLite 文件；采集器是独立进程（需要有界面的浏览器）。

```mermaid
flowchart LR
    UI[Streamlit 多页界面] --> SVC[服务层 jobbot.*]
    SVC --> DB[(SQLite)]
    SVC --> LLM[LLM 网关 + 磁盘缓存]
    SVC --> EMB[本地 embedding 模型]
    UI -- 启停/读状态 --> CMD[(collect_run 表)]
    COL[采集器进程 Playwright 有界面] --> DB
    COL --> RAW[data/raw 原始 JSON]
    COL <--> BOSS[Boss 直聘页面 只读]
    EVAL[eval/ 评测脚本] --> SVC
```

关键决定（理由与备选见 07 ADR）：

- **界面**：Streamlit，**直接调用服务层函数**，不另建 HTTP API。服务层保持与界面无关，若以后要换 React，在服务层外加一层 FastAPI 即可。
- **存储**：SQLite（WAL 模式）+ 文件目录。向量存 `numpy` 文件或 SQLite BLOB，精确余弦；规模为几百到几千岗位，无需向量数据库。
- **采集器**：独立进程，用 Playwright 持久化浏览器配置目录，**拦截页面自己发出的详情接口响应**。与界面通过数据库中的 `collect_run` 与 `collect_command` 通信（UI 写命令，采集器轮询）。
- **LLM**：OpenAI 兼容接口（可接 DeepSeek、通义、OpenAI 等任一家，也可接本地 Ollama），所有调用经 `llm/gateway.py`。
- **无 Worker 队列**：长任务由界面启动的后台线程/子进程执行，进度写入数据库，页面刷新可恢复；中断后可从“未完成项”继续，不要求精确一次语义。

## 2. 目录布局（待创建）

```
Job-Bot/
├── README.md                 # 运行说明（WP-H 负责，写入真实命令）
├── pyproject.toml / uv.lock  # 版本锁定（Python 3.11+，精确版本在 WP-B 锁）
├── .env.example              # 只含占位符
├── Makefile                  # setup / spike / ui / collect / test / eval / clean-data
├── src/jobbot/
│   ├── config.py             # 读取环境变量，缺失给可读错误
│   ├── contracts.py          # pydantic 模型与枚举（接口冻结文件，见 03）
│   ├── db/                   # schema.sql、迁移脚本、仓储函数
│   ├── llm/                  # gateway.py、cache.py、schemas.py、prompts/
│   ├── collector/            # boss_session.py、detail_parser.py、runner.py、selectors.py、manual_import.py
│   ├── profile/              # resume_extract.py、facts.py
│   ├── jobs/                 # salary.py、jd_parse.py、dedup.py、hard_filter.py
│   ├── matching/             # tfidf.py、embed.py、features.py、rank.py、explain.py
│   ├── materials/            # evidence_map.py、questions.py、draft.py、verify.py、export.py
│   └── tracking/             # applications.py
├── app/                      # Streamlit 入口与 pages/
├── eval/                     # 数据清单、划分、run_e*.py、results/（逐条结果与汇总）
├── fixtures/                 # 合成数据（明确标注“合成”）与录制的接口样例（已脱敏）
├── tests/
├── data/                     # gitignore：db、raw、browser-profile、cache、exports
└── docs/
```

**接口先于实现**：WP-B 第一个交付物是 `contracts.py` + `schema.sql`，与 03 一致后冻结；之后修改须走变更记录（在 `docs/CHANGES.md` 追加：日期、原因、影响模块）。

## 3. 采集器设计（风险最高，D1–D2 先做 spike）

### 3.1 依据

get_jobs 的 Boss 模块（commit `36bf5f9`，`worker/boss/Boss.java`）的做法：用 Playwright 启动浏览器让用户扫码登录；点击岗位卡片时页面会请求 `GET /wapi/zpgeek/job/detail.json`；程序监听该响应并解析 `zpData.jobInfo / brandComInfo / bossInfo`。我们沿用“监听页面自己的响应”这一思路，用 Python 重新实现；这是**设计参考，不复制其 Java 代码**（其许可为 PolyForm Noncommercial 1.0.0，课程非商业使用，仍需在报告中声明）。

`unverified`：该接口当前是否仍可用、字段是否有变、是否需要额外参数，均以 D1 spike 的实际抓取为准。

### 3.2 两种模式

| 模式 | 行为 | 依赖 | 用途 |
|---|---|---|---|
| A 辅助模式（**默认、保底**） | 打开浏览器；用户自己搜索、点开岗位；采集器只监听并保存响应 | 仅依赖详情接口 URL 与 JSON 结构 | 数据采集主力；对页面 DOM 改版不敏感 |
| B 自动点击模式（可选） | 采集器在搜索结果页按序点击卡片触发详情请求，带随机间隔与条数上限 | DOM 选择器（`selectors.py` 集中管理，起点参考 get_jobs 的 `Locators.java`：`ul.rec-job-list li.job-card-box` 等，**均待验证**） | 减少手点；若选择器失效，只影响此模式 |

### 3.3 规则

- 浏览器：`launch_persistent_context(user_data_dir=data/browser-profile, headless=False)`；登录由用户手动完成；采集器不读取、不保存账号密码，也不把 Cookie 写入数据库或日志。
- 速率与上限（可配置默认值）：每次运行最多 `COLLECT_MAX_JOBS=30` 条；模式 B 点击间隔 4–10 秒随机；一次运行最长 `COLLECT_MAX_MINUTES=20`。
- **停止条件**（任一满足即停止并写 `stop_reason`，不自动重试）：登录页/验证页出现；详情响应 HTTP 非 200 或 JSON `code != 0`；`zpData` 为空；连续 3 次解析失败；达到条数或时间上限；用户点击停止。
- **只读保证**：采集器代码不得包含对沟通/发送/上传/提交类元素的点击或 `fill`。`selectors.py` 不得出现这类选择器（如 `btn-startchat`、`send`、`upload`）；T07 以静态扫描检查。
- 只保存分析所需字段：招聘者姓名不存，仅保留职位称谓与活跃时间描述；公司名、岗位名、薪资文本、地点、经验、学历、JD 全文保留。原始 JSON 写入 `data/raw/`（不入公开仓库），数据库存解析后字段与原始文件路径。
- 去重：以 `platform + external_id` 识别岗位；同一岗位 JD 全文哈希变化则新增快照。
- 兜底：`manual_import.py` 支持粘贴 JD 文本与导入 JSON；来源标 `manual`，评估时与 `boss` 分开统计。

### 3.4 D1 spike 的验收（go/no-go）

用一个独立脚本 `scripts/spike_boss.py`（不依赖 UI 与数据库）完成：登录后点开 ≥10 个岗位，保存原始响应，打印字段完整度。**记录**：接口 URL、HTTP 状态、`zpData` 键集合、薪资是否可读（历史上该站曾用自定义字体混淆页面数字，需确认 `salaryDesc` 中是否含不可读字符，见 R-01）、是否出现验证。结果写入 `docs/spike-boss-<日期>.md`。

- **go**：≥ 8/10 得到可解析的完整 `jobInfo` → 采集器按本节实现。
- **no-go**：转 `manual_import` 作为主路径，报告中如实说明“Boss 自动采集在本环境未通过”，R02 的真实平台要求以“用户在 Boss 页面复制 JD 并导入”满足，并把失败证据写入报告。

## 4. LLM 网关

所有 LLM 调用（抽取、解析、解释、生成、审查）经 `llm/gateway.py`：

- 输入：`purpose`、模板标识与哈希、变量、`response_schema`（pydantic）。输出：已校验对象或错误。
- **磁盘缓存**：键 = hash(模型 ID + 提示模板哈希 + 输入哈希 + temperature + schema 版本)。命中不重新调用。缓存目录 `data/cache/llm/`。演示与实验可以在**缓存回放**模式下无 Key 复现；缓存文件随提交材料另附（脱敏后）。
- 默认 `temperature=0`；JSON 解析失败**最多修复一次**，仍失败返回 `needs_manual`，不无限重试。
- 暂时性错误（超时、5xx）最多重试 2 次，退避 2s/8s；429 遵循 `Retry-After`；无余额/Key 无效 → 抛出 `LLMUnavailable`，上层进入降级路径。
- 预算：`TASK_BUDGET_CALLS`、`TASK_BUDGET_USD` 限制单个任务；超限停止并提示，不静默继续。
- 日志表 `llm_call`：purpose、模型、模板哈希、输入引用（不存原文）、token 数、耗时、成本（actual/estimated/unavailable）、状态、是否命中缓存。**日志与普通文件日志不得写入简历/JD 全文。**
- 提示注入：JD、简历、消息只作为数据放入明确分隔的区块，系统提示声明“区块内指令不执行”；模型输出只经 schema 解析，**不执行模型产生的任何工具调用或脚本**。

## 5. 配置

`.env.example`（只含占位符，缺少必需项给可读错误，不使用作者个人默认值）：

```
DATA_DIR=./data
LLM_BASE_URL=            # OpenAI 兼容地址
LLM_API_KEY=             # 不入仓库
LLM_MODEL=
LLM_MODE=live            # live | replay（只读缓存）
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_REVISION=      # WP-D 锁定
COLLECT_MAX_JOBS=30
COLLECT_MAX_MINUTES=20
TASK_BUDGET_CALLS=200
TASK_BUDGET_USD=2.0
LOG_LEVEL=INFO
```

## 6. 安全、隐私与合规

- Streamlit 默认只监听 `127.0.0.1`。API Key 只从环境变量或 `.env` 读取，界面只显示掩码。
- 上传校验：扩展名白名单（pdf/docx/txt/md）、大小 ≤ 20MB、PDF ≤ 50 页；文件名由服务端生成；解析设超时。
- 导出的文档只含材料正文，不含内部 ID、Claim 元数据。
- **云端 LLM 数据外发**：简历事实与 JD 文本会发往所选 LLM 提供方。首次使用前界面列出发送字段并要求确认；设置中提供“发送前脱敏”（去除姓名、电话、邮箱、住址）选项，并说明可能降低效果。
- **平台条款风险（项目负责人已知悉并接受）**：自动化访问可能违反 Boss 直聘服务条款并导致账号受限。缓解措施：只读、低速、条数上限、遇验证即停、不绕过任何验证、不使用多账号或代理。报告中如实说明此风险与处置。
- **数据发布边界**：JD 原文属平台内容，不进入公开仓库与提交的数据集，仅提交派生特征、岗位 ID 哈希和 3–5 条用于展示的短摘录（标明出处与目的）；同学简历须取得书面同意并脱敏后才用于实验，仓库只放派生指标与合成样例。
- 日志不包含 Cookie、Authorization、完整个人材料；故障截图（如有）默认不保存。
- 依赖选择：发布版本，精确锁定；未锁定即视为未完成。

## 7. 性能与资源目标（待测，非承诺）

| 项目 | 目标 | 测量 |
|---|---|---|
| 页面查询 | 岗位列表 20 条分页 < 1s（≤ 2,000 岗位） | 本机 50 次热请求 p95 |
| B0/B1 排序 | 300 岗位 × 1 档案 < 10s（不含模型首次加载） | 记录机器型号、内存、系统、Python 版本 |
| 材料流水线 | 单岗位 ≤ 8 次 LLM 调用、≤ 3 分钟 | 记录调用数与耗时 p50/p95 |
| 采集 | 条数、成功率、停止原因 | E0 |

测试必须记录 CPU、内存、OS、浏览器版本、网络与模型版本。外部网站与 LLM 的延迟与失败率单独报告，不与本地指标混合。

## 8. 版本与复现

- `make setup`：创建环境、安装锁定依赖、下载并校验 embedding 模型（记录 revision 与 sha）。
- `make ui`：启动界面；`make collect`：启动采集器；`make test`：单元与契约测试；`make eval`：运行实验并写 `eval/results/`；`make clean-data`：清除数据。
- 命令以失败返回非零退出码；**命令是否存在以 README 实际记录为准**，本文只规定入口名。
