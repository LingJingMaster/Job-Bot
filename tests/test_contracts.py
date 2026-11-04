from pydantic import ValidationError

from jobbot.contracts import (
    EXPLANATION_SCORE_NOTE,
    DraftBullet,
    EvidenceLink,
    FactStructured,
    HardConstraint,
    JobSnapshotDraft,
    Requirement,
    RequirementNormalized,
    Salary,
    SourceRef,
)


def test_null_is_not_empty_list_and_zero_salary_is_rejected() -> None:
    unknown = FactStructured(skills=None, metrics=None)
    confirmed_empty = FactStructured(skills=[], metrics=[])
    assert unknown.skills is None
    assert confirmed_empty.skills == []
    assert unknown.skills != confirmed_empty.skills

    Salary(salary_text="面议", min=None, max=None, salary_unit=None, months=None)
    try:
        Salary(min=0)
    except ValidationError as exc:
        assert "0" in str(exc)
    else:
        raise AssertionError("薪资 0 应被拒绝")


def test_hard_constraint_defaults_to_needs_review() -> None:
    constraint = HardConstraint(field="city", operator="eq", value="上海")
    assert constraint.unknown_policy == "needs_review"


def test_overlong_jd_is_rejected_without_truncation() -> None:
    try:
        JobSnapshotDraft(platform="manual", source="manual", description="甲" * 100_001)
    except ValidationError as exc:
        assert "100000" in str(exc)
        assert "未截断" in str(exc)
    else:
        raise AssertionError("超长 JD 应被拒绝")
    JobSnapshotDraft(platform="manual", source="manual", description="甲" * 100_000)


def test_inferred_requirement_cannot_be_hard_and_covered_needs_fact() -> None:
    try:
        Requirement(
            id="r1",
            text="熟悉 Python",
            type="hard",
            weight=3,
            inferred=True,
            source_ref=SourceRef(span=[0, 4]),
            normalized=RequirementNormalized(skills=["Python"]),
        )
    except ValidationError as exc:
        assert "硬条件" in str(exc)
    else:
        raise AssertionError("推断项不应成为硬条件")

    try:
        EvidenceLink(requirement_id="r1", status="covered", fact_ids=[], rationale="没有事实")
    except ValidationError as exc:
        assert "fact_id" in str(exc)
    else:
        raise AssertionError("covered 缺少事实应失败")

    try:
        DraftBullet(text="主导了上线", fact_ids=[], requirement_ids=[], type="factual")
    except ValidationError as exc:
        assert "fact_ids" in str(exc)
    else:
        raise AssertionError("无事实的 factual bullet 应失败")


def test_score_note_denies_hire_probability() -> None:
    assert "不是录用概率" in EXPLANATION_SCORE_NOTE
