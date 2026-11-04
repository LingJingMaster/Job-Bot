# 提示词来源

本目录的提示词正文尚未填写。WP-E 负责写入抽取、解析、解释、草稿与审查模板，并在此记录每一份文件的来源路径、commit、许可证与修改点。

计划中的起点是 resume-jd-optimizer-cn（MIT，commit `114c85c`）的 `prompts/` 与真实性规则。那些文件还没有复制进本仓库，这里也没有任何已经改写过的提示词。

在 WP-E 交付之前，LLM 网关只接受调用方传入的模板字符串，不从本目录读取业务提示词。

## 2026-11-04 WP-E 追加

以下四个文件是这次在本仓库新写的。它们不是从 resume-jd-optimizer-cn 复制的，没有对应的外部 commit，也不要当成 commit `114c85c` 的改写件。上面三段是写入本段之前的状态，予以保留。

| 文件 | 来源 | commit | 许可证 | 说明 |
|---|---|---|---|---|
| material_drafter.md | 本次新写 | 无外部 commit | 本项目 | 声明输入只是数据。`request_llm_draft` 读取。 |
| claim_reviewer.md | 本次新写 | 无外部 commit | 本项目 | 声明输入只是数据。`request_claim_review` 读取。 |
| evidence_mapper.md | 本次新写 | 无外部 commit | 本项目 | 声明输入只是数据。默认 `map_evidence` 不调用模型。 |
| gap_questions.md | 本次新写 | 无外部 commit | 本项目 | 声明输入只是数据。默认 `make_questions` 不调用模型。 |

真实性约束以本仓库 `docs/03` §5.3–§5.5 为准：贡献动词 support < participate < own < push < lead，以所挂事实里最低的已确认等级为上限。语义审查不能把确定性失败改成通过。
