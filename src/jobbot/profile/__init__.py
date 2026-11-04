"""简历事实与求职偏好。"""

from jobbot.profile.extract import extract_facts
from jobbot.profile.facts import (
    add_manual_fact,
    confirm_fact,
    ensure_profile,
    freeze_profile,
    list_claims,
    list_facts,
    list_snapshots,
    retract_fact,
)
from jobbot.profile.ingest import get_document, ingest_resume, list_documents
from jobbot.profile.preference import latest_preference, save_preference

__all__ = [
    "add_manual_fact",
    "confirm_fact",
    "ensure_profile",
    "extract_facts",
    "freeze_profile",
    "get_document",
    "ingest_resume",
    "latest_preference",
    "list_claims",
    "list_documents",
    "list_facts",
    "list_snapshots",
    "retract_fact",
    "save_preference",
]
