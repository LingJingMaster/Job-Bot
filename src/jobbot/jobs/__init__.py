"""岗位薪资、快照、JD 解析、去重提示和硬条件。"""

from jobbot.jobs.aliases import canonical_city, canonical_skill, load_aliases, skills_equivalent
from jobbot.jobs.dedup import DuplicateSuggestion, dedup
from jobbot.jobs.hard_filter import hard_filter
from jobbot.jobs.jd_parse import jd_quality, parse_jd
from jobbot.jobs.salary import parse_salary
from jobbot.jobs.snapshots import upsert_snapshot

__all__ = [
    "DuplicateSuggestion",
    "canonical_city",
    "canonical_skill",
    "dedup",
    "hard_filter",
    "jd_quality",
    "load_aliases",
    "parse_jd",
    "parse_salary",
    "skills_equivalent",
    "upsert_snapshot",
]
