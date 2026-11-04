"""岗位与匹配。没有档案或岗位时只给下一步，不显示已排序。"""

from __future__ import annotations

import streamlit as st

from jobbot.config import load_config
from jobbot.contracts import ConfigError, JobBotError
from jobbot.matching.embed import embedding_status
from jobbot.matching.explain import explain
from jobbot.matching.rank import MIN_LABELS, l0, rank
from jobbot.matching.store import (
    job_titles,
    label_map,
    latest_preference_for,
    list_job_snapshots,
    list_profile_snapshots,
    list_profiles,
    load_run,
)

_METHOD_LABELS = {
    "b0": "B0 字符重合",
    "b1": "B1 向量",
    "b2": "B2 特征加权",
    "b3": "B3 逻辑回归",
    "l0": "L0 模型评分",
}
_GROUP_LABELS = {
    "recommend": "默认推荐",
    "needs_review": "需复核",
    "hard_fail": "硬条件未通过（已保存，不进入默认推荐）",
}


def _show_pending() -> None:
    error = st.session_state.pop("error", None)
    notice = st.session_state.pop("notice", None)
    if error:
        st.error(error)
    if notice:
        st.info(notice)


st.header("岗位与匹配")
st.caption("排序分不是录用概率。不按姓名、性别、年龄或婚育排序。")

try:
    load_config()
except ConfigError as exc:
    st.error(exc.user_text())
    st.stop()

_show_pending()
profiles = list_profiles()
if not profiles:
    st.info("还没有档案。本次没有排序，也没有写入排序结果。下一步：到简历与事实页确认事实并生成档案快照。")
    st.stop()

profile_id = str(profiles[0]["id"])
snapshots = list_profile_snapshots(profile_id)
if not snapshots:
    st.info("还没有确认后的档案快照。本次没有排序，未确认事实不会进入排序。下一步：确认事实后生成档案快照。")
    st.stop()
preference = latest_preference_for(profile_id)
if preference is None:
    st.info("还没有求职偏好。本次没有排序。下一步：先保存偏好。")
    st.stop()
jobs = list_job_snapshots()
if not jobs:
    st.info("还没有岗位。本次没有排序。下一步：采集或手动导入岗位描述。")
    st.stop()

profile = snapshots[-1]
ready, embedding_message = embedding_status()
if not ready:
    st.warning(embedding_message + " 当前可以运行 B0。")
label_count = len(label_map(profile_id, None))
if label_count < MIN_LABELS:
    st.warning(
        f"当前标注 {label_count} 条，不足 {MIN_LABELS}。"
        "若运行 B3，运行记录会写成方向性实验。"
    )

method = st.selectbox("排序方法", list(_METHOD_LABELS), format_func=lambda item: _METHOD_LABELS[item])
if st.button("开始排序"):
    try:
        cfg = {"corpus_ids": [job.id for job in jobs]}
        if method == "l0":
            finished = l0(profile, jobs, preference, cfg)
        else:
            finished = rank(method, profile, jobs, preference, cfg)
        st.session_state["run_id"] = finished.id
        st.session_state["notice"] = (
            "排序记录已写入。分数不是录用概率。"
            "硬条件未通过的岗位已保存，但不在默认推荐里。"
        )
    except JobBotError as exc:
        st.session_state["error"] = exc.user_text()
    st.rerun()

run_id = st.session_state.get("run_id")
if not run_id:
    st.info("这次会话还没有运行排序。")
    st.stop()
run = load_run(str(run_id))
if run is None:
    st.error("找不到刚写入的排序记录。没有把它显示成完成。下一步：重新运行。")
    st.stop()

st.subheader("本次排序")
st.text(f"方法 {run.method}，档案快照 {run.profile_snapshot_id}，岗位 {run.n_jobs} 个。")
st.caption("排序分不是录用概率。")
logistic = run.config.get("logistic") if isinstance(run.config, dict) else None
if isinstance(logistic, dict) and logistic.get("directional_experiment"):
    st.warning(str(logistic.get("note") or "这是方向性实验。"))
if run.method == "llm":
    st.caption(str(run.config.get("note") or "L0 不保证与 B0–B3 一样可复现。"))

titles = job_titles()
grouped = {"recommend": [], "needs_review": [], "hard_fail": []}
for result in run.results:
    group = "recommend"
    if isinstance(result.components, dict):
        group = str(result.components.get("display_group") or group)
    grouped.setdefault(group, []).append(result)

for group_name in ("recommend", "needs_review", "hard_fail"):
    rows = grouped.get(group_name) or []
    st.subheader(_GROUP_LABELS.get(group_name, group_name))
    if not rows:
        st.text("这一组没有岗位。")
        continue
    ordered = sorted(rows, key=lambda item: (item.rank is None, item.rank or 10**9, item.snapshot_id))
    for result in ordered:
        title = titles.get(result.snapshot_id, result.snapshot_id)
        score_text = "没有分数" if result.score is None else f"{result.score:.4f}"
        rank_text = "未进入默认名次" if result.rank is None else str(result.rank)
        st.text(f"{title}  名次 {rank_text}  分数 {score_text}  硬条件 {result.hard_result}")
        if isinstance(result.components, dict) and result.components.get("weight_note"):
            st.caption(str(result.components["weight_note"]))
        explanation = explain(result)
        st.text("匹配：" + ("；".join(explanation.matched) or "没有可引用的匹配点"))
        st.text("差距：" + ("；".join(explanation.gaps) or "没有已记录的差距"))
        st.text("未知：" + ("；".join(explanation.unknowns) or "没有已记录的未知项"))
        st.caption(explanation.score_note)
