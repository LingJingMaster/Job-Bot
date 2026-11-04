"""要求到已确认事实的映射。无效引用被丢弃并计数，不进入结果。"""

from __future__ import annotations

from jobbot.contracts import EvidenceLink, Fact, JDParse, ProfileSnapshot, Requirement
from jobbot.materials.store import citation_ok, connect_db, load_documents, row_to_fact
from jobbot.materials.text_rules import clip_rationale, fact_skill_names
from jobbot.db.repository import get_by_id

_NOT_APPLICABLE_MARKERS = ("不要求", "无需", "不适用")


class EvidenceLinkList(list[EvidenceLink]):
    def __init__(
        self,
        items: list[EvidenceLink],
        *,
        discarded: int,
        requirements: dict[str, Requirement],
    ) -> None:
        super().__init__(items)
        self.discarded = discarded
        self.requirements = requirements


def map_evidence(parse: JDParse, profile: ProfileSnapshot) -> list[EvidenceLink]:
    requirements = list(parse.requirements or [])
    with connect_db() as connection:
        facts, discarded = _usable_facts(connection, profile.fact_ids)
    links: list[EvidenceLink] = []
    requirement_index = {requirement.id: requirement for requirement in requirements}
    for requirement in requirements:
        links.append(_link_for(requirement, facts))
    result = EvidenceLinkList(links, discarded=discarded, requirements=requirement_index)
    for link in result:
        assert len(link.rationale) <= 60
        if link.status == "covered":
            assert link.fact_ids
        if link.status == "not_applicable":
            assert link.rationale.strip()
    return result


def _usable_facts(connection, fact_ids: list[str]) -> tuple[list[Fact], int]:
    documents = load_documents(connection)
    usable: list[Fact] = []
    discarded = 0
    for fact_id in fact_ids:
        row = get_by_id(connection, "fact", fact_id)
        if row is None or row["status"] != "confirmed":
            discarded += 1
            continue
        fact = row_to_fact(row)
        if not citation_ok(fact, documents):
            discarded += 1
            continue
        usable.append(fact)
    return usable, discarded


def _link_for(requirement: Requirement, facts: list[Fact]) -> EvidenceLink:
    if any(marker in requirement.text for marker in _NOT_APPLICABLE_MARKERS):
        return EvidenceLink(
            requirement_id=requirement.id,
            status="not_applicable",
            fact_ids=[],
            rationale=clip_rationale("岗位写明不要求此项"),
        )
    matched = [fact for fact in facts if _matches(requirement, fact)]
    non_skill = [fact for fact in matched if fact.category != "skill"]
    skill_only = [fact for fact in matched if fact.category == "skill"]
    if non_skill:
        used = non_skill
        status = "covered"
        rationale = clip_rationale(f"对应事实：{used[0].statement}")
    elif skill_only:
        used = skill_only
        status = "weak"
        rationale = clip_rationale("该技能只出现在技能列表，最多记为弱对应")
    else:
        used = []
        status = "missing"
        rationale = clip_rationale("没有已确认事实能对应")
    return EvidenceLink(
        requirement_id=requirement.id,
        status=status,
        fact_ids=[fact.id for fact in used],
        rationale=rationale,
    )


def _matches(requirement: Requirement, fact: Fact) -> bool:
    required = {skill.casefold() for skill in requirement.normalized.skills if skill.strip()}
    if required:
        return bool(required & fact_skill_names(fact))
    compact = "".join(requirement.text.split())
    statement = "".join(fact.statement.split())
    return len(compact) >= 4 and compact in statement
