"""岗位采集页。浏览器只在点击启动后由采集进程打开。"""

from __future__ import annotations

import json
import subprocess
import sys

import streamlit as st

from jobbot.collector.database import database_path, open_database
from jobbot.collector.manual_import import import_manual
from jobbot.collector.runs import start_run, stop_run
from jobbot.collector.runner import _load_playwright
from jobbot.collector.stop_conditions import REASON_TEXT
from jobbot.config import load_config
from jobbot.contracts import CollectLimits, ConfigError, JobBotError
from jobbot.db.repository import list_rows

STATUS_TEXT = {
    "running": "进行中",
    "finished": "已结束",
    "stopped": "已停止",
    "failed": "未能完成",
}


def render_collect_page() -> None:
    st.header("岗位采集")
    st.caption("辅助模式只监听你自己点开的岗位。登录由你完成。程序不代为沟通，也不代为投递。")
    try:
        settings = load_config()
    except ConfigError as exc:
        st.error(exc.user_text())
        st.stop()

    st.markdown(
        f"默认上限：{settings.collect_max_jobs} 条详情，{settings.collect_max_minutes} 分钟。"
        "达到上限、验证页或连续解析失败都会停下，并且不会自动再请求。"
    )
    keyword = st.text_input("关键词，可留空", value="")
    runs = _runs()
    snapshots = _snapshot_count()
    _show_runs(runs, snapshots)

    if st.button("启动采集"):
        _start(keyword.strip() or None, settings.collect_max_jobs, settings.collect_max_minutes)
    if st.button("停止采集"):
        _stop(runs)

    st.subheader("粘贴导入")
    st.caption("可以粘贴职位描述，或粘贴详情 JSON。两种都填时不会导入。")
    pasted = st.text_area("职位描述", height=180)
    pasted_json = st.text_area("JSON", height=180)
    if st.button("导入"):
        _import(pasted, pasted_json)
    notice = st.session_state.get("collect_notice")
    if notice:
        level, text = notice
        getattr(st, level)(text)


def _show_runs(runs: list[dict], snapshots: int) -> None:
    if not runs:
        st.info(
            f"还没有浏览器采集记录。岗位库里有 {snapshots} 条快照。"
            "浏览器没有打开，也没有把未运行的采集写成完成。数据未保存。"
            if snapshots == 0
            else f"还没有浏览器采集记录。岗位库里已有 {snapshots} 条手动或历史快照。当前没有正在进行的采集。"
        )
        return
    latest = runs[-1]
    status = STATUS_TEXT.get(str(latest["status"]), str(latest["status"]))
    st.markdown(f"最近一次状态：{status}")
    st.markdown(
        f"看到 {latest['n_seen']} 条，写入 {latest['n_ok']} 条，失败 {latest['n_fail']} 条。"
        f"岗位库共 {snapshots} 条快照。"
    )
    reason = latest.get("stop_reason")
    if reason:
        st.markdown(f"停止原因：{REASON_TEXT.get(reason, reason)}（{reason}）")
    elif latest["status"] == "running":
        st.info("采集仍标记为进行中。本页不把进行中写成已经结束。")
    else:
        st.info("这次记录没有停止原因。")


def _start(keyword: str | None, max_jobs: int, max_minutes: int) -> None:
    runs = _runs()
    if runs and runs[-1]["status"] == "running":
        st.session_state["collect_notice"] = (
            "warning",
            "已有进行中的采集。没有再打开一个浏览器。",
        )
        return
    try:
        _load_playwright()
        run_id = start_run("assist", keyword, CollectLimits(max_jobs=max_jobs, max_minutes=max_minutes))
        subprocess.Popen(
            [sys.executable, "-m", "jobbot.collector.runner", "--run-id", run_id],
            start_new_session=True,
        )
    except JobBotError as exc:
        st.session_state["collect_notice"] = ("error", exc.user_text())
        return
    st.session_state["collect_notice"] = (
        "info",
        f"已写入启动命令，运行编号 {run_id}。请在打开的窗口里自己登录并点开岗位。",
    )


def _stop(runs: list[dict]) -> None:
    if not runs or runs[-1]["status"] != "running":
        st.session_state["collect_notice"] = (
            "warning",
            "没有正在进行的采集。没有写入停止命令。",
        )
        return
    try:
        stop_run(str(runs[-1]["id"]))
    except JobBotError as exc:
        st.session_state["collect_notice"] = ("error", exc.user_text())
        return
    st.session_state["collect_notice"] = (
        "info",
        "已写入停止命令。采集进程收到后会停下，已保存的岗位仍保留。",
    )


def _import(pasted: str, pasted_json: str) -> None:
    text = pasted.strip()
    raw_json = pasted_json.strip()
    before = _snapshot_count()
    try:
        if text and raw_json:
            raise JobBotError(
                "职位描述和 JSON 都填了。请只保留一种。数据未保存。",
                saved=False,
                next_step="清空其中一栏后再导入。",
            )
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise JobBotError(
                    f"JSON 无法解析：{exc.msg}。数据未保存。",
                    saved=False,
                    next_step="检查括号和引号后再导入。",
                ) from exc
            if not isinstance(parsed, dict):
                raise JobBotError(
                    "JSON 顶层必须是对象。数据未保存。",
                    saved=False,
                    next_step="使用对象形式的 JSON。",
                )
            import_manual(json_obj=parsed)
        else:
            import_manual(text=text or None)
    except JobBotError as exc:
        st.session_state["collect_notice"] = ("error", exc.user_text())
        return
    after = _snapshot_count()
    if after == before:
        st.session_state["collect_notice"] = (
            "info",
            "相同内容已在岗位库中，没有新增快照。",
        )
        return
    st.session_state["collect_notice"] = (
        "info",
        f"已保存 1 条快照。来源是手动导入。岗位库现在有 {after} 条。这不是浏览器采集的结果。",
    )


def _rows(table: str) -> list[dict]:
    if not database_path().exists():
        return []
    with open_database() as connection:
        return list_rows(connection, table)


def _runs() -> list[dict]:
    return _rows("collect_run")


def _snapshot_count() -> int:
    return len(_rows("job_snapshot"))


render_collect_page()
