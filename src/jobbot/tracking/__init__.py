"""手动申请跟踪。阶段只由用户更改。"""

from jobbot.tracking.applications import (
    STAGE_LABEL,
    STAGE_ORDER,
    ApplicationEventRecord,
    ApplicationRecord,
    MaterialChoice,
    PageData,
    StageUpdate,
    TrackableJob,
    list_events,
    load_page,
    record_manual_stage,
    save_manual_stage,
)

__all__ = [
    "STAGE_LABEL",
    "STAGE_ORDER",
    "ApplicationEventRecord",
    "ApplicationRecord",
    "MaterialChoice",
    "PageData",
    "StageUpdate",
    "TrackableJob",
    "list_events",
    "load_page",
    "record_manual_stage",
    "save_manual_stage",
]
