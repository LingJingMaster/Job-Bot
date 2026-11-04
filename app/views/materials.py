"""材料工作台。要求–证据、追问、草稿、校验、批准、导出。"""

from __future__ import annotations

import streamlit as st

from jobbot.config import load_config
from jobbot.contracts import ApprovalBlocked, ConfigError, JobBotError, Material
from jobbot.materials import (
    approve,
    draft_material,
    export,
    make_questions,
    map_evidence,
    revise_material,
    save_questions,
    verify_claims,
)
from jobbot.materials.store import (
    latest_jd_parse,
    list_claims,
    list_job_snapshots,
    list_materials_for,
    list_profile_snapshots,
    load_facts,
)

STATUS_LABEL = {
    "draft": "草稿",
    "needs_input": "待补充",
    "verified": "已校验",
    "approved": "已批准",
    "superseded": "已替代",
}
EVIDENCE_LABEL = {
    "covered": "覆盖",
    "weak": "弱对应",
    "missing": "缺失",
    "not_applicable": "不适用",
}
CHECK_LABEL = {
    "supported": "有依据",
    "unsupported": "无依据",
    "needs_review": "待复核",
}
REASON_LABEL = {
    "number_mismatch": "数字与事实不一致",
    "verb_escalation": "贡献动词强于事实",
    "unknown_skill": "事实中没有该技能",
    "date_conflict": "日期或时间线冲突",
    "org_mismatch": "公司或机构与事实不一致",
    "no_fact": "没有有效事实",
    "semantic_unsupported": "语义审查未通过",
}


def render() -> None:
    st.header("材料工作台")
    try:
        load_config()
        profiles = list_profile_snapshots()
        jobs = list_job_snapshots()
    except ConfigError as exc:
        st.error(exc.user_text())
        return
    except JobBotError as exc:
        st.error(exc.user_text())
        return
    if not profiles or not jobs:
        st.info("还没有已确认档案或岗位快照。本次没有生成材料。数据未保存。下一步：先确认事实并冻结档案，或导入岗位。")
        st.warning("未批准")
        return

    profile_by_id = {item.id: item for item in profiles}
    job_by_id = {item.id: item for item in jobs}
    profile_id = st.selectbox(
        "档案快照",
        list(profile_by_id),
        format_func=lambda item_id: f"修订 {profile_by_id[item_id].revision}",
    )
    job_id = st.selectbox(
        "岗位快照",
        list(job_by_id),
        format_func=lambda item_id: job_by_id[item_id].title or "未命名岗位",
    )
    profile = profile_by_id[profile_id]
    job = job_by_id[job_id]
    parsed = latest_jd_parse(job.id)
    links = []
    if parsed is not None and parsed.requirements:
        try:
            links = map_evidence(parsed, profile)
        except JobBotError as exc:
            st.error(exc.user_text())
    questions = make_questions(links) if links else []
    skipped_key = f"skipped-{profile.id}-{job.id}"
    skipped = set(st.session_state.get(skipped_key, []))

    st.subheader("要求–证据")
    if parsed is None or not parsed.requirements:
        st.caption("该岗位还没有 JD 解析，暂时不能映射证据。无效引用不会被展示。")
    else:
        discarded = getattr(links, "discarded", 0)
        if discarded:
            st.caption(f"已丢弃 {discarded} 条无效引用，未在下表展示。")
        fact_by_id = {fact.id: fact for fact in load_facts(profile.fact_ids)}
        requirements = getattr(links, "requirements", {})
        if not links:
            st.caption("没有可展示的要求。")
        for link in links:
            requirement = requirements.get(link.requirement_id)
            title = requirement.text if requirement is not None else link.requirement_id
            weight = requirement.weight if requirement is not None else ""
            snippets = [
                fact_by_id[fact_id].statement
                for fact_id in link.fact_ids
                if fact_id in fact_by_id
            ]
            shown = "；".join(snippets) if snippets else "—"
            st.text(
                f"{EVIDENCE_LABEL.get(link.status, link.status)}｜权重 {weight}｜{title}｜{shown}｜{link.rationale}"
            )

    st.subheader("追问")
    st.caption("跳过或作答都不会新建事实。没有原文就跳过，不会编造经历。")
    if not questions:
        st.caption("这一轮没有需要追问的要求。")
    for question in questions:
        st.text(question.text)
        if question.requirement_id in skipped:
            st.caption("已跳过。没有补造事实。")
        if st.button("跳过", key=f"skip-{profile.id}-{job.id}-{question.requirement_id}"):
            skipped.add(question.requirement_id)
            st.session_state[skipped_key] = sorted(skipped)
            st.rerun()

    st.subheader("草稿")
    if st.button("生成草稿"):
        try:
            material = draft_material("resume", job, profile, links, "pipeline")
            if questions:
                save_questions(material.id, questions, skipped)
            st.rerun()
        except JobBotError as exc:
            st.error(exc.user_text())

    materials = [item for item in list_materials_for(profile.id, job.id) if item.status != "superseded"]
    if not materials:
        materials = list_materials_for(profile.id, job.id)
    material = _select_material(materials)
    edited = ""
    if material is None:
        st.warning("未批准")
        st.text_area("草稿预览（未批准）", value="", disabled=True)
    else:
        if material.status != "approved":
            st.warning("未批准")
        label = "已批准正文" if material.status == "approved" else "草稿预览（未批准）"
        edited = st.text_area(label, value=material.content, key=f"body-{material.id}")
        if st.button("保存修改"):
            try:
                revise_material(material.id, edited)
                st.rerun()
            except JobBotError as exc:
                st.error(exc.user_text())

    st.subheader("校验")
    claims = list_claims(material.id) if material is not None else []
    if material is not None and st.button("校验陈述"):
        try:
            verify_claims(material, profile)
            st.rerun()
        except JobBotError as exc:
            st.error(exc.user_text())
    if not claims:
        st.caption("还没有可校验的陈述。")
    for claim in claims:
        reasons = []
        detail = claim.check_detail or {}
        for reason in detail.get("reasons") or []:
            reasons.append(REASON_LABEL.get(reason, reason))
        reason_text = "、".join(reasons) if reasons else "—"
        st.text(f"{CHECK_LABEL.get(claim.check_status, claim.check_status)}｜{reason_text}｜{claim.text}")

    st.subheader("批准")
    block = _approval_block(material, claims)
    if block:
        st.caption(block)
    if st.button("批准", disabled=block is not None, key="approve-material"):
        try:
            assert material is not None
            approve(material.id)
            st.rerun()
        except (ApprovalBlocked, JobBotError) as exc:
            st.error(exc.user_text())

    st.subheader("导出")
    st.caption("PDF 导出本轮未做，不会生成 PDF 文件。导出只含正文。")
    export_disabled = material is None or material.status != "approved" or _claims_block_export(claims)
    if export_disabled:
        st.caption("未批准或仍有无依据陈述，不能导出终稿。")
    _export_button("导出 DOCX", "docx", material, export_disabled)
    _export_button("导出 TXT", "txt", material, export_disabled)
    _export_button("导出 PDF", "pdf", material, export_disabled)


def _select_material(materials: list[Material]) -> Material | None:
    if not materials:
        return None
    ordered = sorted(materials, key=lambda item: (item.revision, item.created_at or "", item.id))
    labels = {item.id: f"修订 {item.revision} · {STATUS_LABEL.get(item.status, item.status)}" for item in ordered}
    selected_id = st.selectbox("材料版本", [item.id for item in ordered], format_func=lambda item_id: labels[item_id])
    return labels and next(item for item in ordered if item.id == selected_id)


def _approval_block(material: Material | None, claims) -> str | None:
    if material is None:
        return "还没有草稿，不能批准。"
    if material.status == "approved":
        return "这份材料已批准。要改内容会另存为新版本，已批准正文保持不变。"
    if not claims or any(claim.check_status in {"unsupported", "needs_review"} for claim in claims):
        return "存在无依据或待复核的陈述，不能批准。"
    if material.status != "verified":
        return "尚未完成校验，不能批准。"
    return None


def _claims_block_export(claims) -> bool:
    return not claims or any(claim.check_status in {"unsupported", "needs_review"} for claim in claims)


def _export_button(label: str, fmt: str, material: Material | None, disabled: bool) -> None:
    if st.button(label, disabled=disabled, key=f"export-{fmt}"):
        if material is None:
            st.error("没有可导出的材料。终稿未写出。下一步：先批准。")
            return
        try:
            path = export(material.id, fmt)  # type: ignore[arg-type]
        except (ApprovalBlocked, JobBotError) as exc:
            st.error(exc.user_text())
            return
        st.info(f"已写出文件：{path.name}。文件名绑定正文 hash，文件里只有正文。")


render()
