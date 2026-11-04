"""盲标页。随机顺序，每次一条。不读取、不显示排序结果。"""

from __future__ import annotations

import random

import streamlit as st

from jobbot.config import load_config
from jobbot.contracts import ConfigError, JobBotError
from jobbot.labeling import (
    HARD_LABELS,
    RATING_DETAILS,
    RATING_TEXT,
    RATINGS,
    ROUNDS,
    confirmed_statements,
    has_job_snapshots,
    list_pending_jobs,
    list_profile_choices,
    open_db,
    ordered_jobs,
    preference_lines,
    save_label,
)

_ROUND_TEXT = {
    "first": "first 首次",
    "recheck": "recheck 间隔复标",
    "second": "second 第二人",
}
_HARD_TEXT = {
    "pass": "pass 硬条件满足",
    "fail": "fail 硬条件不满足",
    "unclear": "unclear 暂不能判断",
}
_EMPTY = "没有待标岗位。本次没有打分。数据未保存。"


def _show_pending() -> None:
    error = st.session_state.pop("blind_error", None)
    notice = st.session_state.pop("blind_notice", None)
    if error:
        st.error(error)
    if notice:
        st.info(notice)


def _seed() -> int:
    if "blind_seed" not in st.session_state:
        st.session_state["blind_seed"] = random.SystemRandom().randrange(1, 2_147_483_647)
    return int(st.session_state["blind_seed"])


def _skipped() -> list[str]:
    current = st.session_state.get("blind_skipped")
    if not isinstance(current, list):
        current = []
        st.session_state["blind_skipped"] = current
    return [str(item) for item in current]


def _labeler_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


st.header("标注与实验")
st.subheader("盲标")
st.markdown(
    "\n".join(
        [
            "评分含义：",
            RATING_TEXT[0],
            RATING_TEXT[1],
            RATING_TEXT[2],
            RATING_TEXT[3],
            RATING_DETAILS[0],
            RATING_DETAILS[1],
            RATING_DETAILS[2],
            RATING_DETAILS[3],
        ]
    )
)
st.caption(
    "顺序已打乱，每次只显示一条。页面上只有岗位描述原文、已确认事实和偏好。"
    "跳过不会写成 0 分，也不会写入标注。"
    "本页不生成一致性报告，也没有运行评测。"
)
st.caption("硬条件看地点、学历、经验等是否满足。依据写在备注里。")

try:
    load_config()
except ConfigError as exc:
    st.error(exc.user_text())
    st.stop()

_show_pending()
seed = _seed()

with open_db() as connection:
    profiles = list_profile_choices(connection)
    jobs_exist = has_job_snapshots(connection)

if not profiles or not jobs_exist:
    st.info(_EMPTY)
    st.stop()

if len(profiles) == 1:
    profile_id = profiles[0][0]
    st.text(f"当前档案：{profiles[0][1]}")
else:
    profile_ids = [item[0] for item in profiles]
    profile_names = dict(profiles)
    profile_id = st.selectbox("档案", profile_ids, format_func=lambda item: profile_names[item])
    st.text(f"当前档案：{profile_names[profile_id]}")

labeler = _labeler_text(st.text_input("标注者", key="blind_labeler"))
round_name = st.selectbox(
    "轮次",
    list(ROUNDS),
    format_func=lambda item: _ROUND_TEXT[item],
    key="blind_round",
)

with open_db() as connection:
    pending = list_pending_jobs(connection, profile_id, labeler, round_name)
    facts = confirmed_statements(connection, profile_id)
    preferences = preference_lines(connection, profile_id)

skipped = set(_skipped())
queue = [job for job in ordered_jobs(pending, seed) if job.snapshot_id not in skipped]
if not queue:
    st.info(_EMPTY)
    st.stop()

job = queue[0]
st.subheader("岗位描述原文")
st.text(job.jd_text)
st.subheader("已确认事实")
if facts:
    for statement in facts:
        st.text(statement)
else:
    st.text("还没有已确认事实。")
st.subheader("偏好")
for line in preferences:
    st.text(line)

rating = st.selectbox(
    "评分",
    list(RATINGS),
    format_func=lambda item: RATING_TEXT[item],
    key="blind_rating",
)
hard_label = st.selectbox(
    "硬条件",
    list(HARD_LABELS),
    format_func=lambda item: _HARD_TEXT[item],
    key="blind_hard",
)
note = st.text_area("备注", key="blind_note")

if st.button("保存这条标注", key=f"save-{job.snapshot_id}"):
    try:
        with open_db() as connection:
            save_label(
                connection,
                profile_id=profile_id,
                snapshot_id=job.snapshot_id,
                rating=rating,
                hard_label=hard_label,
                labeler=labeler,
                round=round_name,
                note=note,
            )
        st.session_state["blind_notice"] = "这条标注已写入。"
    except JobBotError as exc:
        st.session_state["blind_error"] = exc.user_text()
    st.rerun()

if st.button("跳过", key=f"skip-{job.snapshot_id}"):
    st.session_state["blind_skipped"] = [*_skipped(), job.snapshot_id]
    st.session_state["blind_notice"] = "已跳过当前岗位。跳过没有写成 0 分，也没有写入标注。"
    st.rerun()
