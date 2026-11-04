"""求职偏好。每次保存产生新版本，不覆盖旧行。"""

from __future__ import annotations

from typing import Any

from jobbot.contracts import HardConstraint, JobBotError, Preference
from jobbot.db.repository import get_by_id, insert, list_rows
from jobbot.profile._db import loads_json, open_db


def latest_preference(profile_id: str) -> Preference | None:
    with open_db() as connection:
        rows = [row for row in list_rows(connection, "preference") if row["profile_id"] == profile_id]
    if not rows:
        return None
    return _preference_from_row(max(rows, key=lambda row: int(row["revision"])))


def save_preference(
    profile_id: str,
    hard: list[HardConstraint] | None,
    soft: list[dict[str, Any]] | None = None,
    search_terms: list[str] | None = None,
) -> Preference:
    with open_db() as connection:
        profile = get_by_id(connection, "profile", profile_id)
        if profile is None:
            raise JobBotError(
                "档案不存在。偏好没有保存。",
                saved=False,
                next_step="先上传简历或创建档案。",
            )
        revisions = [
            int(row["revision"])
            for row in list_rows(connection, "preference")
            if row["profile_id"] == profile_id
        ]
        revision = max(revisions, default=0) + 1
        preference_id = insert(
            connection,
            "preference",
            {
                "profile_id": profile_id,
                "revision": revision,
                "hard": [item.model_dump(mode="json") for item in hard] if hard is not None else None,
                "soft": soft,
                "search_terms": search_terms,
            },
        )
        row = get_by_id(connection, "preference", preference_id)
    if row is None:
        raise JobBotError("偏好写入后读不到。", saved=False, next_step="重试保存。")
    return _preference_from_row(row)


def _preference_from_row(row: dict[str, Any]) -> Preference:
    hard_raw = loads_json(row.get("hard"))
    soft_raw = loads_json(row.get("soft"))
    terms_raw = loads_json(row.get("search_terms"))
    hard = (
        [HardConstraint.model_validate(item) for item in hard_raw]
        if isinstance(hard_raw, list)
        else None
    )
    return Preference(
        id=row["id"],
        profile_id=row["profile_id"],
        revision=int(row["revision"]),
        hard=hard,
        soft=soft_raw if isinstance(soft_raw, list) else None,
        search_terms=[str(item) for item in terms_raw] if isinstance(terms_raw, list) else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
