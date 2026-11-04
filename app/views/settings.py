"""设置与状态。"""

from __future__ import annotations

import streamlit as st

from jobbot.capabilities import capability_matrix, collector_implemented
from jobbot.config import load_config, mask_secret
from jobbot.contracts import ConfigError

st.title("设置与状态")

try:
    settings = load_config()
except ConfigError as exc:
    st.error(exc.user_text())
    st.stop()

masked_key = mask_secret(settings.llm_api_key)
embedding_ready = settings.embedding_model_exists()
rows = capability_matrix(
    llm_api_key=settings.llm_api_key,
    llm_base_url=settings.llm_base_url,
    llm_model=settings.llm_model,
    embedding_revision=settings.embedding_revision,
    embedding_model_exists=embedding_ready,
    collector_implemented=collector_implemented(),
)

st.subheader("当前配置")
st.markdown(f"数据目录：`{settings.data_dir}`")
st.markdown(
    f"任务预算：最多 {settings.task_budget_calls} 次调用，"
    f"金额上限 {settings.task_budget_usd} USD。超出后停止，不会静默继续。"
)
st.markdown(f"LLM 模式：{settings.llm_mode}")
st.markdown(f"LLM 地址：{settings.llm_base_url or '（未设置）'}")
st.markdown(f"LLM 模型：{settings.llm_model or '（未设置）'}")
st.markdown(f"LLM API Key：{masked_key}")
st.markdown(
    "Embedding："
    + (
        f"revision `{settings.embedding_revision}`，模型文件已找到。"
        if embedding_ready
        else "revision 为空或模型文件不在，B1/B2/B3 不可用。"
    )
)
if collector_implemented():
    st.markdown(
        "采集器代码已在。模式 A 只监听你自己点开的岗位。"
        "真实 Boss 采集尚未跑过，本页不把它记为已通过。"
    )
else:
    st.markdown("采集器状态：尚未实现。不会报告正在采集或已经采集成功。")
st.caption("匹配分不是录用概率。本页只展示依赖是否满足，不执行匹配。")

if not settings.llm_api_key:
    st.warning(
        "未配置 LLM_API_KEY。自动抽取、解释、草稿生成不可用；"
        "手动录入事实、JD 粘贴、B0 和规则校验仍可用。B1 还需要本地 embedding。"
        "匹配分不是录用概率。"
    )
if not settings.llm_base_url or not settings.llm_model:
    st.warning("LLM_BASE_URL 或 LLM_MODEL 为空。没有内置服务商地址。需要调用模型时会提示缺少配置，不会假装已连通。")

st.subheader("能力矩阵")
for row in rows:
    st.markdown(f"**{row.name}**：{row.status}。{row.reason}")

if st.button("保存配置"):
    st.warning("保存配置尚未实现。没有写入 .env，也没有把 Key 记入仓库。下一步：在本机 .env 中修改。")
if st.button("连接诊断"):
    st.warning("连接诊断尚未实现。没有发出网络请求，不能把未连接说成已连通。")
if st.button("清除数据"):
    st.warning("此按钮不会删除数据。请在仓库根目录运行 make clean-data。当前没有清除任何文件。")
