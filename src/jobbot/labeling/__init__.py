"""盲标：保存人工标注、打乱待标岗位、计算二次加权 κ。"""

from jobbot.labeling.agreement import quadratic_weighted_kappa
from jobbot.labeling.blind import (
    confirmed_statements,
    has_job_snapshots,
    list_pending_jobs,
    list_profile_choices,
    ordered_jobs,
    preference_lines,
    shuffled_ids,
)
from jobbot.labeling.db import open_db
from jobbot.labeling.store import (
    HARD_LABELS,
    RATING_DETAILS,
    RATING_TEXT,
    RATINGS,
    ROUNDS,
    save_label,
)

__all__ = [
    "HARD_LABELS",
    "RATING_DETAILS",
    "RATING_TEXT",
    "RATINGS",
    "ROUNDS",
    "confirmed_statements",
    "has_job_snapshots",
    "list_pending_jobs",
    "list_profile_choices",
    "open_db",
    "ordered_jobs",
    "preference_lines",
    "quadratic_weighted_kappa",
    "save_label",
    "shuffled_ids",
]
