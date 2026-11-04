"""缺口追问。每轮最多 5 题。跳过或作答都不会新建事实。"""

from __future__ import annotations

from jobbot.contracts import EvidenceLink, JobBotError, Question, Requirement
from jobbot.db.connection import utc_now_iso
from jobbot.db.repository import get_by_id, insert, update_by_id
from jobbot.materials.store import connect_db, load_requirement_index, row_to_question
from jobbot.materials.text_rules import QUESTION_LIMIT


def make_questions(links: list[EvidenceLink], max_n: int = 5) -> list[Question]:
    limit = min(max_n, QUESTION_LIMIT)
    if limit < 0:
        limit = 0
    assert limit <= QUESTION_LIMIT
    index = _requirement_map(links)
    eligible: list[tuple[int, str, str]] = []
    for link in links:
        if link.status not in {"weak", "missing"}:
            continue
        requirement = index.get(link.requirement_id)
        weight = requirement.weight if requirement is not None else 0
        if weight < 2:
            continue
        text = requirement.text if requirement is not None else link.requirement_id
        eligible.append((weight, link.requirement_id, text))
    eligible.sort(key=lambda item: (-item[0], item[1]))
    chosen = eligible[:limit]
    questions = [
        Question(
            requirement_id=requirement_id,
            text=(
                f"关于要求「{text}」，如果简历原文里有对应经历，请指出原句。"
                "没有就可以跳过，跳过不会编造事实。"
            ),
        )
        for _weight, requirement_id, text in chosen
    ]
    assert len(questions) <= QUESTION_LIMIT
    return questions


def save_questions(
    material_id: str,
    questions: list[Question],
    skipped_requirement_ids: set[str] | None = None,
) -> list[Question]:
    skipped = skipped_requirement_ids or set()
    saved: list[Question] = []
    with connect_db() as connection:
        for question in questions:
            answer = "跳过" if question.requirement_id in skipped else question.answer
            row_id = insert(
                connection,
                "question",
                {
                    "material_id": material_id,
                    "requirement_id": question.requirement_id,
                    "text": question.text,
                    "answer": answer,
                    "answered_at": utc_now_iso() if answer else None,
                    "resulting_fact_id": None,
                },
            )
            row = get_by_id(connection, "question", row_id)
            assert row is not None
            saved.append(row_to_question(row))
    for question in saved:
        assert question.resulting_fact_id is None
    return saved


def skip_question(question_id: str) -> Question:
    """记下跳过。不插入 fact，也不把回答写成候选事实。"""
    return _store_answer(question_id, "跳过")


def record_answer(question_id: str, answer: str) -> Question:
    """只把回答记在 question 上。确认事实仍由档案模块处理，这里不新建事实。"""
    cleaned = answer.strip()
    if cleaned == "" or cleaned == "跳过":
        return skip_question(question_id)
    return _store_answer(question_id, cleaned)


def _store_answer(question_id: str, answer: str) -> Question:
    with connect_db() as connection:
        current = get_by_id(connection, "question", question_id)
        if current is None:
            raise JobBotError(
                "追问不存在。没有记下回答，也没有新建事实。",
                saved=False,
                next_step="先生成这一轮追问。",
            )
        update_by_id(
            connection,
            "question",
            question_id,
            {
                "answer": answer,
                "answered_at": utc_now_iso(),
                "resulting_fact_id": None,
            },
        )
        row = get_by_id(connection, "question", question_id)
    assert row is not None
    question = row_to_question(row)
    assert question.resulting_fact_id is None
    return question


def _requirement_map(links: list[EvidenceLink]) -> dict[str, Requirement]:
    attached = getattr(links, "requirements", None)
    if isinstance(attached, dict) and attached:
        return attached
    return load_requirement_index()
