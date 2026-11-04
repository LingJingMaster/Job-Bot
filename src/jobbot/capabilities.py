"""依赖层面的能力矩阵。纯函数，不访问网络，不宣称业务已经跑通。"""

from __future__ import annotations

from pathlib import Path

from jobbot.contracts import EXPLANATION_SCORE_NOTE, Capability

_COLLECTOR_RUNNER = Path(__file__).resolve().parent / "collector" / "runner.py"


def collector_implemented() -> bool:
    return _COLLECTOR_RUNNER.is_file()


def capability_matrix(
    *,
    llm_api_key: str,
    llm_base_url: str = "",
    llm_model: str = "",
    embedding_revision: str,
    embedding_model_exists: bool,
    collector_implemented: bool,
) -> list[Capability]:
    has_key = bool(llm_api_key.strip())
    has_endpoint = bool(llm_base_url.strip() and llm_model.strip())
    revision = embedding_revision.strip()
    has_embedding = bool(revision) and embedding_model_exists
    llm_ready = has_key and has_endpoint

    if not revision:
        embedding_block = "EMBEDDING_REVISION 为空，本地 embedding 模型未锁定。"
    elif not embedding_model_exists:
        embedding_block = "EMBEDDING_REVISION 已填写，但模型文件不在 data/models/<revision>。"
    else:
        embedding_block = ""

    def llm_item(name: str, action: str) -> Capability:
        if not has_key:
            return Capability(
                name=name,
                status="不可用",
                reason=f"未配置 LLM_API_KEY，{action}不可用。可继续使用不依赖模型的功能。",
            )
        if not has_endpoint:
            return Capability(
                name=name,
                status="不可用",
                reason=f"已配置 LLM_API_KEY，但 LLM_BASE_URL 或 LLM_MODEL 为空，{action}不可用。没有使用内置服务商。",
            )
        return Capability(name=name, status="可用", reason=f"LLM Key、地址和模型已配置，{action}的依赖已满足。")

    def embedding_item(name: str, detail: str) -> Capability:
        if has_embedding:
            return Capability(
                name=name,
                status="可用",
                reason=f"{detail}本地 embedding 已就绪。{EXPLANATION_SCORE_NOTE}。",
            )
        return Capability(
            name=name,
            status="不可用",
            reason=f"{embedding_block}{name} 需要本地 embedding，当前不可用。B0 不依赖该模型。",
        )

    rows = [
        Capability(
            name="手动录入事实",
            status="可用",
            reason="手动录入不依赖 LLM Key，无 Key 时仍可用。",
        ),
        llm_item("自动抽取", "自动抽取"),
        Capability(
            name="JD 粘贴",
            status="可用",
            reason="粘贴 JD 不依赖采集器和 LLM，采集器不可用时仍可用。",
        ),
        Capability(
            name="导入 JSON",
            status="可用",
            reason="导入 JSON 不依赖采集器和 LLM，采集器不可用时仍可用。",
        ),
        Capability(
            name="Boss 自动采集",
            status="可用" if collector_implemented else "不可用",
            reason=(
                "采集器代码已存在。只读监听，不代为投递。"
                if collector_implemented
                else "采集器尚未实现，不能从 Boss 自动采集。请改用 JD 粘贴或导入 JSON。"
            ),
        ),
        Capability(
            name="B0",
            status="可用",
            reason=f"B0 使用字符 TF-IDF，不依赖 LLM Key，也不依赖本地 embedding。{EXPLANATION_SCORE_NOTE}。",
        ),
        embedding_item("B1", "B1 使用本地 embedding 余弦。无 LLM Key 时只要模型文件在就可以用。"),
        embedding_item("B2", "B2 的语义特征依赖本地 embedding。"),
        embedding_item("B3", "B3 的特征依赖本地 embedding。模型文件在并不表示已经完成训练。"),
        llm_item("解释", "解释"),
        llm_item("草稿生成", "草稿生成"),
        Capability(
            name="手动材料编辑",
            status="可用",
            reason="手动编辑材料不依赖 LLM Key。",
        ),
        Capability(
            name="规则校验",
            status="可用",
            reason="规则校验检查事实、数字、日期和贡献动词，不依赖 LLM Key。",
        ),
    ]
    degraded = (not llm_ready) or (not has_embedding) or (not collector_implemented)
    reasons: list[str] = []
    if not has_key:
        reasons.append("未配置 LLM_API_KEY：自动抽取、解释、草稿生成不可用")
    elif not has_endpoint:
        reasons.append("LLM 地址或模型名为空：自动抽取、解释、草稿生成不可用")
    if not has_embedding:
        reasons.append(embedding_block + "B1/B2/B3 不可用，B0 可用")
    if not collector_implemented:
        reasons.append("采集器尚未实现：Boss 自动采集不可用，粘贴和导入仍可用")
    rows.insert(
        0,
        Capability(
            name="运行模式",
            status="降级" if degraded else "可用",
            reason=(
                "；".join(reasons) + f"。{EXPLANATION_SCORE_NOTE}。"
                if degraded
                else f"LLM、本地 embedding 与采集器依赖均已满足。{EXPLANATION_SCORE_NOTE}。"
            ),
        ),
    )
    return rows
