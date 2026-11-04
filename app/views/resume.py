"""简历与事实：上传、候选确认、撤销和偏好。"""

from __future__ import annotations

import streamlit as st

from jobbot.config import load_config
from jobbot.contracts import ConfigError, FactEdits, HardConstraint, JobBotError, LLMUnavailable
from jobbot.profile import (
    add_manual_fact,
    confirm_fact,
    ensure_profile,
    extract_facts,
    freeze_profile,
    ingest_resume,
    latest_preference,
    list_claims,
    list_documents,
    list_facts,
    list_snapshots,
    retract_fact,
    save_preference,
)

_STATUS_LABEL = {
    "candidate": "候选（未确认）",
    "confirmed": "已确认",
    "retracted": "已撤销",
}
_CATEGORIES = ["education", "work", "project", "skill", "award", "certificate", "other"]
_CONTRIBUTIONS = ["", "support", "participate", "own", "push", "lead"]


def _show_pending() -> None:
    error = st.session_state.pop("error", None)
    notice = st.session_state.pop("notice", None)
    if error:
        st.error(error)
    if notice:
        st.info(notice)


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]


st.header("简历与事实")
st.caption("候选事实不会自动确认，确认之前不会进入材料。")

try:
    settings = load_config()
except ConfigError as exc:
    st.error(exc.user_text())
    st.stop()

if not settings.llm_api_key.strip():
    st.warning(
        "未配置 LLM_API_KEY。自动抽取不可用，本次不会把它显示成已完成。"
        "简历原文仍可以保存。下一步：手动录入事实，或配置 Key 后再抽取。"
    )

_show_pending()
profile_id = ensure_profile()

st.subheader("上传简历")
upload = st.file_uploader("选择 pdf、docx、txt 或 md", type=["pdf", "docx", "txt", "md"])
if st.button("上传并抽取候选事实", key="upload_resume"):
    if upload is None:
        st.session_state["error"] = "没有选择文件。本次没有写入简历。下一步：选择 pdf、docx、txt 或 md。"
    else:
        try:
            stored = ingest_resume(upload.name, bytes(upload.getvalue()))
            try:
                facts = extract_facts(stored.id)
                st.session_state["notice"] = (
                    f"简历原文已保存，服务器文件名已生成。得到 {len(facts)} 条候选事实，"
                    "状态为待确认，没有进入材料。下一步：逐条确认、修改或拒绝。"
                )
            except LLMUnavailable as exc:
                st.session_state["error"] = (
                    "自动抽取已降级，没有写入候选事实，也没有把这次调用当成完成。"
                    f"简历原文仍在。{exc.user_text()}"
                )
            except JobBotError as exc:
                st.session_state["error"] = exc.user_text()
        except JobBotError as exc:
            st.session_state["error"] = exc.user_text()
    st.rerun()

documents = list_documents()
st.subheader("已上传文件")
if not documents:
    st.info("还没有简历。数据未新增。下一步：上传 pdf、docx、txt 或 md。")
else:
    for document in documents:
        st.text(
            f"{document.id}  状态 {document.parse_status}  路径 {document.path}"
        )
        if document.parse_error:
            st.text(document.parse_error)
        if document.text:
            st.text(document.text)

st.subheader("事实")
facts = list_facts(profile_id)
if not facts:
    st.info("还没有事实。未确认的内容不会进入材料。下一步：抽取或在下方手动录入。")
for fact in facts:
    st.text(f"[{_STATUS_LABEL.get(fact.status, fact.status)}] 第 {fact.revision} 版  {fact.category}")
    st.text(fact.statement)
    if fact.source_ref is not None:
        st.text(f"定位 {fact.source_ref.locator or '未标注'}")
        st.text(fact.source_ref.excerpt or "")
    st.text(
        "贡献 "
        + (fact.contribution or "未填写")
        + ("；估算" if fact.is_estimate else "；非估算")
    )
    if fact.status == "candidate":
        if st.button("确认", key=f"confirm-{fact.id}"):
            try:
                confirm_fact(fact.id)
                st.session_state["notice"] = "这条事实已记为已确认。它仍不会自动进入材料。下一步：需要定稿时生成确认版本。"
            except JobBotError as exc:
                st.session_state["error"] = exc.user_text()
            st.rerun()
        if st.button("拒绝", key=f"reject-{fact.id}"):
            try:
                retract_fact(fact.id, "用户拒绝该候选事实")
                st.session_state["notice"] = "已拒绝这条候选事实，状态改为已撤销。没有把它写入确认版本。下一步：可继续处理其他候选。"
            except JobBotError as exc:
                st.session_state["error"] = exc.user_text()
            st.rerun()
    if fact.status == "confirmed":
        reason = st.text_input("撤销原因", key=f"reason-{fact.id}")
        if st.button("撤销", key=f"retract-{fact.id}"):
            try:
                material_ids = retract_fact(fact.id, reason)
                claims = list_claims(material_ids)
                claim_text = "；".join(claim.text for claim in claims) or "没有挂接的 Claim"
                affected = "、".join(material_ids) or "没有"
                st.session_state["notice"] = (
                    f"已撤销事实并生成新的档案快照。受影响材料：{affected}。"
                    f"这些材料已标为需要补充。Claim：{claim_text}。"
                    "已批准版本的正文没有改写，仍可查看。下一步：核对材料后再决定是否重新起草。"
                )
            except JobBotError as exc:
                st.session_state["error"] = exc.user_text()
            st.rerun()
    if fact.status != "retracted":
        with st.form(f"edit-{fact.id}"):
            statement = st.text_input("修改后的陈述", value=fact.statement)
            contribution = st.selectbox("贡献", _CONTRIBUTIONS, index=_CONTRIBUTIONS.index(fact.contribution or ""))
            submitted = st.form_submit_button("保存修改并确认")
        if submitted:
            try:
                confirm_fact(
                    fact.id,
                    FactEdits(
                        statement=statement,
                        contribution=contribution or None,  # type: ignore[arg-type]
                    ),
                )
                st.session_state["notice"] = "修改已另存为新版本并标为已确认。旧版本正文还在。下一步：生成确认版本后再给材料使用。"
            except JobBotError as exc:
                st.session_state["error"] = exc.user_text()
            st.rerun()

if st.button("生成确认版本", key="freeze_profile"):
    try:
        snapshot = freeze_profile(profile_id)
        st.session_state["notice"] = (
            f"已生成确认版本第 {snapshot.revision} 版，包含 {len(snapshot.fact_ids)} 条已确认事实。"
            "候选事实没有写进这个版本。下一步：到匹配或材料页时使用这个版本。"
        )
    except JobBotError as exc:
        st.session_state["error"] = exc.user_text()
    st.rerun()

snapshots = list_snapshots(profile_id)
if snapshots:
    latest = snapshots[-1]
    st.text(f"当前确认版本第 {latest.revision} 版，事实 {len(latest.fact_ids)} 条。更早的版本仍保留。")

st.subheader("手动录入")
st.caption("无模型时用这里录入。摘录必须能在来源正文里逐字找到，保存后仍是候选。")
with st.form("manual_fact"):
    manual_statement = st.text_input("陈述", key="manual_statement")
    manual_excerpt = st.text_area("原文摘录", key="manual_excerpt")
    manual_source = st.text_area("来源正文", key="manual_source")
    manual_category = st.selectbox("类别", _CATEGORIES, key="manual_category")
    manual_contribution = st.selectbox("贡献（可空）", _CONTRIBUTIONS, key="manual_contribution")
    manual_estimate = st.checkbox("这是估算", key="manual_estimate")
    manual_submit = st.form_submit_button("保存为候选事实")
if manual_submit:
    try:
        add_manual_fact(
            profile_id,
            category=manual_category,
            statement=manual_statement,
            excerpt=manual_excerpt,
            contribution=manual_contribution or None,
            is_estimate=manual_estimate,
            source_text=manual_source,
        )
        st.session_state["notice"] = "手动事实已保存，状态为候选，尚未确认。下一步：在列表里确认或拒绝。"
    except JobBotError as exc:
        st.session_state["error"] = exc.user_text()
    st.rerun()

st.subheader("求职偏好")
current = latest_preference(profile_id)
if current is None:
    st.info("还没有偏好。未知条件不会被当成不满足。下一步：填写后保存，旧匹配结果不会被覆盖。")
else:
    st.text(f"当前偏好第 {current.revision} 版。再保存会新增一版，不会改掉旧版。")

with st.form("preference_form"):
    cities = st.text_input("偏好城市（多个用逗号分隔）", key="pref_cities")
    degree = st.text_input("学历不低于", key="pref_degree")
    experience = st.text_input("经验年限不低于", key="pref_experience")
    salary = st.text_input("月薪下限（千元/月）", key="pref_salary")
    excluded = st.text_input("排除词（逗号分隔）", key="pref_exclude")
    unknown_policy = st.selectbox("未知项策略", ["needs_review", "pass", "fail"], key="pref_unknown")
    soft_terms = st.text_input("软偏好关键词", key="pref_soft")
    search_terms = st.text_input("搜索词", key="pref_search")
    preference_submit = st.form_submit_button("保存偏好")
if preference_submit:
    try:
        hard: list[HardConstraint] = []
        city_values = _split_csv(cities)
        if city_values:
            hard.append(
                HardConstraint(field="city", operator="in", value=city_values, unknown_policy=unknown_policy)
            )
        if degree.strip():
            hard.append(
                HardConstraint(field="degree", operator="gte", value=degree.strip(), unknown_policy=unknown_policy)
            )
        if experience.strip():
            hard.append(
                HardConstraint(
                    field="experience_years",
                    operator="gte",
                    value=int(experience.strip()),
                    unknown_policy=unknown_policy,
                )
            )
        if salary.strip():
            hard.append(
                HardConstraint(
                    field="salary_min",
                    operator="gte",
                    value=int(salary.strip()),
                    unknown_policy=unknown_policy,
                )
            )
        exclude_values = _split_csv(excluded)
        if exclude_values:
            hard.append(
                HardConstraint(
                    field="keyword_exclude",
                    operator="not_contains",
                    value=exclude_values,
                    unknown_policy=unknown_policy,
                )
            )
        saved = save_preference(
            profile_id,
            hard,
            soft=[{"field": "keyword", "value": term} for term in _split_csv(soft_terms)] or None,
            search_terms=_split_csv(search_terms) or None,
        )
        st.session_state["notice"] = (
            f"偏好已保存为第 {saved.revision} 版。旧版本还在，匹配结果没有被改写。"
            "下一步：到岗位与匹配页重新匹配。"
        )
    except ValueError:
        st.session_state["error"] = "经验年限或月薪下限不是整数。偏好没有保存。下一步：这两项留空或填写整数。"
    except JobBotError as exc:
        st.session_state["error"] = exc.user_text()
    st.rerun()
