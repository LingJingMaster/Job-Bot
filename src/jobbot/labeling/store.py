"""把一条人工标注写入 label。跳过不经过这里，因此不会变成 0 分。"""

from __future__ import annotations

import logging
import sqlite3

from jobbot.contracts import JobBotError
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, list_rows

logger = logging.getLogger("jobbot.labeling")

RATINGS = (0, 1, 2, 3)
RATING_TEXT = {
    0: "0 不合适",
    1: "1 部分相关",
    2: "2 基本合适",
    3: "3 高度合适",
}
RATING_DETAILS = {
    0: "0 不合适。方向或硬条件明显不符。",
    1: "1 部分相关。关键条件待确认。",
    2: "2 基本合适。值得投递。",
    3: "3 高度合适。优先投递。",
}
HARD_LABELS = ("pass", "fail", "unclear")
ROUNDS = ("first", "recheck", "second")

_DUPLICATE = (
    "同一档案、同一岗位快照、同一标注者和同一轮次已经有标注，不能插入第二行。"
    "已有的那一行没有被覆盖。"
)


def save_label(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    snapshot_id: str,
    rating: int,
    hard_label: str,
    labeler: str,
    round: str,
    note: str | None = None,
) -> str:
    clean_labeler = _required_text(labeler, "标注者不能为空。标注没有写入。")
    clean_round = _choice(round, ROUNDS, "轮次只能是 first、recheck 或 second。这次没有写入。")
    clean_hard = _choice(
        hard_label,
        HARD_LABELS,
        "硬条件标注只能是 pass、fail 或 unclear。这次没有写入。",
    )
    clean_rating = _rating(rating)
    clean_note = _note(note)
    if get_by_id(connection, "profile", profile_id) is None:
        raise JobBotError(
            "档案不存在。标注没有写入。",
            saved=False,
            next_step="先建立档案。",
        )
    if get_by_id(connection, "job_snapshot", snapshot_id) is None:
        raise JobBotError(
            "岗位快照不存在。标注没有写入。",
            saved=False,
            next_step="先导入岗位。",
        )
    _reject_duplicate(connection, profile_id, snapshot_id, clean_labeler, clean_round)
    try:
        label_id = insert(
            connection,
            "label",
            {
                "profile_id": profile_id,
                "snapshot_id": snapshot_id,
                "rating": clean_rating,
                "hard_label": clean_hard,
                "labeler": clean_labeler,
                "labeled_at": utc_now_iso(),
                "note": clean_note,
                "round": clean_round,
            },
        )
    except JobBotError as exc:
        if "UNIQUE" in str(exc):
            raise JobBotError(
                _DUPLICATE,
                saved=False,
                next_step="更换轮次或标注者后再写。",
            ) from exc
        raise
    logger.info(
        "label_saved id=%s profile=%s snapshot=%s round=%s",
        label_id,
        profile_id,
        snapshot_id,
        clean_round,
    )
    return label_id


def _rating(rating: int) -> int:
    if isinstance(rating, bool) or not isinstance(rating, int) or rating not in RATINGS:
        raise JobBotError(
            "评分只能是 0、1、2 或 3。这次没有写入。",
            saved=False,
            next_step="选择一档评分后再保存。跳过不要写成 0 分。",
        )
    return rating


def _choice(value: str, allowed: tuple[str, ...], message: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise JobBotError(message, saved=False, next_step="按页面上的选项重选。")
    return value


def _required_text(value: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JobBotError(message, saved=False, next_step="填写标注者后再保存。")
    return value.strip()


def _note(note: str | None) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise JobBotError(
            "备注必须是文字。这次没有写入。",
            saved=False,
            next_step="把依据写成一段文字，或留空。",
        )
    stripped = note.strip()
    return stripped or None


def _reject_duplicate(
    connection: sqlite3.Connection,
    profile_id: str,
    snapshot_id: str,
    labeler: str,
    round_name: str,
) -> None:
    for row in list_rows(connection, "label"):
        if (
            row["profile_id"] == profile_id
            and row["snapshot_id"] == snapshot_id
            and row["labeler"] == labeler
            and row["round"] == round_name
        ):
            raise JobBotError(
                _DUPLICATE,
                saved=False,
                next_step="更换轮次或标注者后再写。",
            )
