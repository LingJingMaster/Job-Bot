"""从校验器判断重算 E5 计数。

`perturb_statement` 只供测试构造句子，命令本身不会调用它，也不会把样例写进 eval/results。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from metrics import count_e5, default_input, run_input_cli

PERTURBATION_KINDS = ("number", "verb", "skill", "org_or_date", "causal")
_YEAR = re.compile(r"\d{4}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
_ORG = re.compile(r"[\u4e00-\u9fff]{1,20}(?:公司|集团|大学|学院|银行|医院|研究所)")
_VERB_SWAPS = (("参与", "主导"), ("协助", "负责"), ("配合", "带领"))
_EXTRA_SKILLS = ("Kubernetes", "CUDA")


def perturb_statement(text: str, kind: str) -> str:
    """对一条陈述做一种确定性改写。只返回字符串，不写文件。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("扰动需要非空陈述。")
    if kind not in PERTURBATION_KINDS:
        raise ValueError(f"未知扰动类型：{kind}")
    if kind == "number":
        changed = _perturb_number(text)
    elif kind == "verb":
        changed = _perturb_verb(text)
    elif kind == "skill":
        changed = _perturb_skill(text)
    elif kind == "org_or_date":
        changed = _perturb_org_or_date(text)
    else:
        changed = text + "，因此独立促成该结果"
    assert changed != text
    return changed


def main(argv: list[str] | None = None) -> int:
    return run_input_cli(
        argv,
        description="从校验器判断重算 E5 计数",
        flag="--judgments",
        default=default_input("e5", "judgments.jsonl"),
        compute=count_e5,
        missing_noun="校验器判断",
        empty_message="校验器判断是空的，没有可计数的行。",
    )


def _perturb_number(text: str) -> str:
    match = _NUMBER.search(text)
    if match is None:
        return text + "，共 12 项"
    raw = match.group(0)
    percent = raw.endswith("%")
    body = raw[:-1] if percent else raw
    if "." in body:
        bumped = str(float(body) + 1).rstrip("0").rstrip(".")
    else:
        bumped = str(int(body) + 1)
    if percent:
        bumped += "%"
    return text[: match.start()] + bumped + text[match.end() :]


def _perturb_verb(text: str) -> str:
    for source, target in _VERB_SWAPS:
        if source in text:
            return text.replace(source, target, 1)
    return "主导" + text


def _perturb_skill(text: str) -> str:
    for skill in _EXTRA_SKILLS:
        if skill.casefold() not in text.casefold():
            return text + f"，并使用 {skill}"
    return text + "，并使用 Hadoop"


def _perturb_org_or_date(text: str) -> str:
    year = _YEAR.search(text)
    if year is not None:
        replacement = "1999" if year.group(0) != "1999" else "2001"
        return text[: year.start()] + replacement + text[year.end() :]
    org = _ORG.search(text)
    if org is not None:
        return text[: org.start()] + "另一公司" + text[org.end() :]
    return text + "（另一公司，1999）"


if __name__ == "__main__":
    raise SystemExit(main())
