"""申请跟踪。改阶段只写本机记录。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from jobbot.contracts import ConfigError, JobBotError
from jobbot.tracking import (
    STAGE_LABEL,
    STAGE_ORDER,
    ApplicationEventRecord,
    MaterialChoice,
    PageData,
    StageUpdate,
    TrackableJob,
    load_page,
    save_manual_stage,
)

POLICY = (
    "更改阶段只写入本机数据库。招聘平台上的投递、沟通和上传都由你自己完成。"
    "「已投递」只表示你声明自己已经投过，系统不核验，也不向任何外部地址发出请求，也不会自动推进阶段。"
)
EMPTY = (
    "还没有岗位。本次没有建立申请，没有把任何岗位记成已投递。"
    "数据未保存。下一步：打开「岗位采集」，导入或采集岗位后再回来手动登记。"
)
KIND_LABEL = {
    "resume": "简历",
    "ats_text": "ATS 文本",
    "opening_msg": "开场白",
}
STATUS_LABEL = {
    "draft": "草稿",
    "needs_input": "待补充",
    "verified": "已校验",
    "approved": "已批准",
    "superseded": "已替代",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def render() -> None:
    st.header("申请跟踪")
    st.warning(POLICY)
    try:
        page = load_page()
    except ConfigError as exc:
        st.error(exc.user_text())
        return
    except JobBotError as exc:
        st.error(exc.user_text())
        return
    except OSError as exc:
        st.error(f"数据库无法打开：{exc}。申请没有写入。下一步：检查 DATA_DIR 是否可写。")
        return
    if not page.jobs:
        st.info(EMPTY)
        return

    job_by_id = {job.job_id: job for job in page.jobs}
    job_id = st.selectbox(
        "岗位",
        list(job_by_id),
        format_func=lambda item: _job_label(job_by_id[item]),
        key="tracking-job",
    )
    job = job_by_id[job_id]
    current = next((item for item in page.applications if item.job_id == job_id), None)
    stage_ids = list(STAGE_ORDER)
    stage_index = stage_ids.index(current.stage) if current is not None else 0
    to_stage = st.selectbox(
        "阶段",
        stage_ids,
        index=stage_index,
        format_func=lambda item: STAGE_LABEL[item],
        key=f"tracking-stage-{job_id}",
    )
    reason = st.text_input("原因", key=f"tracking-reason-{job_id}")
    closed_reason = st.text_input(
        "关闭原因",
        key=f"tracking-closed-reason-{job_id}",
        help="只有阶段选「已关闭」时才会写入。",
    )
    material_ids = _material_ids(page, job_id, current.material_id if current is not None else None)
    current_material = ""
    if current is not None and current.material_id in material_ids:
        current_material = current.material_id
    material_id = st.selectbox(
        "使用的材料",
        material_ids,
        index=material_ids.index(current_material),
        format_func=lambda item: "不关联材料" if item == "" else _material_label(page.materials, item),
        key=f"tracking-material-{job_id}",
    )
    note = st.text_area(
        "备注",
        value=current.note or "" if current is not None else "",
        key=f"tracking-note-{job_id}",
    )
    st.caption("保存只改本机记录。可以把阶段改成任意其他阶段。关闭会记下关闭原因；再次打开会保留已有事件。")
    if st.button("保存阶段", key="tracking-save"):
        try:
            result = save_manual_stage(
                job_id=job.job_id,
                snapshot_id=job.snapshot_id,
                to_stage=to_stage,
                reason=reason,
                closed_reason=closed_reason,
                material_id=material_id or None,
                note=note,
            )
        except JobBotError as exc:
            st.session_state["tracking_error"] = exc.user_text()
            st.session_state.pop("tracking_notice", None)
        else:
            st.session_state["tracking_notice"] = _notice(result)
            st.session_state.pop("tracking_error", None)
            try:
                page = load_page()
            except ConfigError as exc:
                st.error(exc.user_text())
                st.info(st.session_state["tracking_notice"])
                return
            except JobBotError as exc:
                st.error(exc.user_text())
                st.info(st.session_state["tracking_notice"])
                return

    error = st.session_state.get("tracking_error")
    notice = st.session_state.get("tracking_notice")
    if error:
        st.error(error)
    if notice:
        st.info(notice)
    _render_board(page)
    _render_timeline(page, job_id)


def _notice(result: StageUpdate) -> str:
    label = STAGE_LABEL[result.application.stage]
    if result.event_written:
        return f"已记下阶段「{label}」。只写入了本机记录，没有联系外部平台，也没有代为投递。"
    return f"阶段仍是「{label}」。没有新增阶段事件，没有联系外部平台。"


def _render_board(page: PageData) -> None:
    st.subheader("看板")
    st.caption("阶段全部由你手动修改。")
    applications = {item.job_id: item for item in page.applications}
    for job in page.jobs:
        application = applications.get(job.job_id)
        state = "尚未记录" if application is None else STAGE_LABEL[application.stage]
        st.text(f"{_job_label(job)}：{state}")


def _render_timeline(page: PageData, job_id: str) -> None:
    st.subheader("时间线")
    current = next((item for item in page.applications if item.job_id == job_id), None)
    if current is None:
        st.caption("这个岗位还没有阶段记录。")
        return
    st.caption(f"当前阶段：{STAGE_LABEL[current.stage]}")
    if current.stage == "closed":
        st.caption(f"关闭原因：{current.closed_reason or '（未填）'}")
    st.caption(f"备注：{current.note or '（未填）'}")
    st.caption(f"使用的材料：{_material_label(page.materials, current.material_id)}")
    events = [event for event in page.events if event.application_id == current.id]
    if not events:
        st.caption("还没有阶段事件。")
        return
    for event in events:
        st.text(_event_line(event))


def _event_line(event: ApplicationEventRecord) -> str:
    previous = "（开始记录）" if event.from_stage is None else STAGE_LABEL[event.from_stage]
    because = event.reason or "（未写原因）"
    return (
        f"{_shanghai(event.at)}  {previous} → {STAGE_LABEL[event.to_stage]}"
        f"  原因：{because}  来源：手动"
    )


def _shanghai(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def _job_label(job: TrackableJob) -> str:
    title = job.title or "未命名岗位"
    company = job.company or "公司未填"
    city = job.city or "城市未填"
    return f"{title} · {company} · {city}"


def _material_ids(page: PageData, job_id: str, current_material_id: str | None) -> list[str]:
    options = [""] + [item.id for item in page.materials if item.job_id == job_id]
    if current_material_id and current_material_id not in options:
        options.append(current_material_id)
    return options


def _material_label(materials: list[MaterialChoice], material_id: str | None) -> str:
    if not material_id:
        return "未关联材料"
    for item in materials:
        if item.id == material_id:
            kind = KIND_LABEL.get(item.kind, item.kind)
            status = STATUS_LABEL.get(item.status, item.status)
            return f"{kind} 第 {item.revision} 版（{status}）"
    return "材料记录已找不到"


render()
