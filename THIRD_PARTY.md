# 第三方清单

记录日期：2026-11-04。本轮读取了 `docs/07-sources-decisions.md` §1–§2、`pyproject.toml`、`uv.lock`、`src/jobbot/llm/prompts/PROVENANCE.md`。

版本以 `uv.lock` 里 `[[package]]` 的 `version` 为准。没有进入锁文件的包不写版本号。本轮没有重新打开上游许可证文件或模型卡；下面凡写“沿用 docs/07”，都是文档已有记载，不是本轮新的许可证核对。本文件不记录实验结果。

## 参考仓库与模型

### loks666/get_jobs

- 名称：loks666/get_jobs
- 版本或 commit：`36bf5f986fad93374d77b99205c7684f8450874a`（docs/07 记为 2026-11-04 克隆的 main 头）
- 来源链接：https://github.com/loks666/get_jobs
- 许可证：PolyForm Noncommercial 1.0.0。沿用 docs/07 记载（该处写明已读 `LICENSE`）。本轮未重新打开许可证文件。
- 用途：设计参考。采集思路（监听详情接口）、字段映射、DOM 选择器起点。本项目用 Python 重写读取路径。
- 是否复制代码：否。设计参考，未复制代码。
- 核验日期：2026-11-04

### coinluu/resume-jd-optimizer-cn

- 名称：coinluu/resume-jd-optimizer-cn
- 版本或 commit：`114c85c32dc77bc9ec7315976a21a1f761868664`（短写 `114c85c`）
- 来源链接：https://github.com/coinluu/resume-jd-optimizer-cn
- 许可证：MIT。沿用 docs/07 记载（该处写明已读 `LICENSE`）。本轮未重新打开许可证文件。
- 用途：docs/07 将其记为提示词与真实性规则的计划起点，对应上游 `prompts/` 与 `docs/resume_truthfulness_policy.md`。
- 是否复制代码：否。未复制其原文件。`src/jobbot/llm/prompts/PROVENANCE.md` 写明本仓库的 `material_drafter.md`、`claim_reviewer.md`、`evidence_mapper.md`、`gap_questions.md` 是本次新写，没有外部 commit，不要当成 commit `114c85c` 的拷贝件或改写件。
- 核验日期：2026-11-04

### CopilotKit/openbot

- 名称：CopilotKit/openbot
- 版本或 commit：未锁定
- 来源链接：https://github.com/CopilotKit/openbot
- 许可证：MIT。沿用 docs/07 记载（来自 v1 文档；该处写明设计参考，当时未重新核实）。本轮未重新打开许可证文件。
- 用途：未使用。docs/07 仅列为设计参考：对话与可操作界面结合、任务过程可观察、统一动作入口。
- 是否复制代码：否。未使用，未复制代码。
- 核验日期：2026-11-04

### Snseam/boss-zhipin-mcp

- 名称：Snseam/boss-zhipin-mcp
- 版本或 commit：未锁定
- 来源链接：https://github.com/Snseam/boss-zhipin-mcp
- 许可证：README 声明 MIT。沿用 docs/07 记载（来自 v1；该处没有写明已读许可证文件）。本轮未重新打开许可证文件。
- 用途：未使用。面向招聘者端；docs/07 只记知其 Playwright 与 MCP 封装方式。
- 是否复制代码：否。未使用，未复制代码。
- 核验日期：2026-11-04

### BAAI/bge-small-zh-v1.5

- 名称：BAAI/bge-small-zh-v1.5
- 版本或 commit：revision 未锁定。模型未下载。
- 来源链接：https://huggingface.co/BAAI/bge-small-zh-v1.5
- 许可证：docs/07 已记载模型卡标注 MIT（v1 记载，并写明锁定时核对）。本轮未打开模型卡，未重新核对。
- 用途：docs/07 列为 B1 embedding 候选。
- 是否复制代码：否。未下载模型，未复制权重或代码。
- 核验日期：2026-11-04

## 文档列出、锁文件中没有的组件

这些包不在 `uv.lock` 中。不写版本号。docs/07 里“锁定时核对”的许可证名称不是已核验结论。

### scikit-learn

- 名称：scikit-learn
- 版本或 commit：未锁定，当前代码未依赖
- 来源链接：https://pypi.org/project/scikit-learn/
- 许可证：docs/07 写明拟在锁定时核对 BSD-3-Clause。当前未锁定。本轮未打开许可证文件，不记为已核验。
- 用途：docs/07 计划用于 TF-IDF、逻辑回归与指标。当前字符 n-gram TF-IDF 在本仓库用标准库实现。
- 是否复制代码：否
- 核验日期：2026-11-04（确认未进入 `uv.lock`）

### Playwright for Python

- 名称：Playwright for Python
- 版本或 commit：未锁定。`pyproject.toml` 与 `uv.lock` 都没有该包，因此没有锁定版本。
- 来源链接：https://pypi.org/project/playwright/
- 许可证：docs/07 写明拟在锁定时核对 Apache-2.0。当前未锁定。本轮未打开许可证文件，不记为已核验。
- 用途：docs/07 计划用于采集器浏览器控制。它不是已锁定依赖。`src/jobbot/collector/runner.py` 只在启动采集时尝试导入，未安装则停止，并且不修改依赖清单。
- 是否复制代码：否
- 核验日期：2026-11-04

### python-docx

- 名称：python-docx
- 版本或 commit：未锁定，当前代码未依赖
- 来源链接：https://pypi.org/project/python-docx/
- 许可证：docs/07 将它列在“逐项核对 / 待锁定”，没有记载已核验的许可证名称。本轮未打开许可证文件。
- 用途：docs/07 将其列为导出候选。当前 DOCX 由标准库写 Office Open XML，未使用该库。
- 是否复制代码：否
- 核验日期：2026-11-04

### PDF 导出库

- 名称：PDF 导出库（未选定具体包）
- 版本或 commit：未锁定，当前代码未依赖
- 来源链接：无。docs/07 没有指定具体库。
- 许可证：未选定，本轮未打开任何 PDF 库的许可证文件。
- 用途：docs/07 列为导出候选。当前 PDF 由标准库手写，没有引入第三方 PDF 库，也没有嵌入字体文件。
- 是否复制代码：否
- 核验日期：2026-11-04

### 中文 PDF 字体

- 名称：中文 PDF 字体（docs/07：待选定）
- 版本或 commit：未选定，未下载
- 来源链接：无已选定的上游地址
- 许可证：docs/07 只要求须允许嵌入与分发，没有记载具体许可证。`src/jobbot/materials/export.py` 的注释写了字体名 Noto Sans CJK SC 和 SIL Open Font License 1.1，并写明本轮不打包字体文件。本轮未打开该字体的许可证文件，不把这段注释当成重新核对。
- 用途：导出时的中文字体候选。字体文件未放入本仓库。
- 是否复制代码：否。未复制字体文件。
- 核验日期：2026-11-04

## `uv.lock` 中的直接依赖

`pyproject.toml` 的运行依赖是 httpx、pydantic、streamlit；开发依赖是 pytest。锁定版本如下。本轮未打开这些包的许可证文件。docs/07 对 Streamlit、pytest、pydantic 只写“逐项核对 / 待锁定”，没有记载已核验的许可证名称；httpx 同样没有已核验名称。

### httpx

- 名称：httpx
- 版本或 commit：0.28.1（声明 `>=0.27,<1`）
- 来源链接：https://pypi.org/project/httpx/0.28.1/
- 许可证：本轮未打开许可证文件。docs/07 未记载已核验名称。
- 用途：HTTP 客户端，供 LLM 网关调用。
- 是否复制代码：否。以依赖安装，未把上游源码复制进本仓库。
- 核验日期：2026-11-04（版本读自 `uv.lock`）

### pydantic

- 名称：pydantic
- 版本或 commit：2.14.2（声明 `>=2.10,<3`）
- 来源链接：https://pypi.org/project/pydantic/2.14.2/
- 许可证：本轮未打开许可证文件。docs/07 未记载已核验名称。
- 用途：数据校验。
- 是否复制代码：否
- 核验日期：2026-11-04（版本读自 `uv.lock`）

### streamlit

- 名称：streamlit
- 版本或 commit：1.66.1（声明 `>=1.40,<2`）
- 来源链接：https://pypi.org/project/streamlit/1.66.1/
- 许可证：本轮未打开许可证文件。docs/07 未记载已核验名称。
- 用途：本地界面。
- 是否复制代码：否
- 核验日期：2026-11-04（版本读自 `uv.lock`）

### pytest

- 名称：pytest
- 版本或 commit：8.4.2（开发依赖，声明 `>=8.3,<9`）
- 来源链接：https://pypi.org/project/pytest/8.4.2/
- 许可证：本轮未打开许可证文件。docs/07 未记载已核验名称。
- 用途：测试。
- 是否复制代码：否
- 核验日期：2026-11-04（版本读自 `uv.lock`）

### numpy（传递依赖，不是直接依赖）

- 名称：numpy
- 版本或 commit：不是 `pyproject.toml` 的直接依赖。`uv.lock` 里它是 streamlit、pandas、pydeck 的传递依赖：Python `< 3.12` 锁定 2.4.6，Python `>= 3.12` 锁定 2.5.4。
- 来源链接：https://pypi.org/project/numpy/
- 许可证：本轮未打开许可证文件。docs/07 未记载已核验名称。
- 用途：docs/07 计划用于向量存储。当前 jobbot 源码没有 import numpy；锁里的 numpy 来自 Streamlit 依赖树。
- 是否复制代码：否
- 核验日期：2026-11-04（版本读自 `uv.lock`）
