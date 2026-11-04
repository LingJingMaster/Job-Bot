"""评测指标的纯函数。

NDCG 与 E1 汇总从 `run_e1.py` 移来，计算公式只有这一份。
计数只读取调用方传入的行，不写 `eval/results`。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


AT_K = 10
RELEVANCE_MIN = 2.0
REPO_ROOT = Path(__file__).resolve().parents[1]

_MODE_NAMES = {
    "A": "A",
    "a": "A",
    "assist": "A",
    "辅助": "A",
    "B": "B",
    "b": "B",
    "auto": "B",
    "自动": "B",
}
_ARMS = {
    "manual": "manual",
    "human": "manual",
    "人工": "manual",
    "纯人工": "manual",
    "system": "system",
    "系统": "system",
}
_FACT_LABELS = {"correct", "partial", "wrong", "hallucination"}
_FACT_LABEL_ALIASES = {
    "正确": "correct",
    "部分": "partial",
    "错误": "wrong",
    "幻觉": "hallucination",
    **{label: label for label in _FACT_LABELS},
}
_KIND_ALIASES = {
    "number": "number",
    "数字": "number",
    "数字改动": "number",
    "verb": "verb",
    "动词": "verb",
    "动词升级": "verb",
    "skill": "skill",
    "技能": "skill",
    "新增技能": "skill",
    "org_or_date": "org_or_date",
    "公司": "org_or_date",
    "日期": "org_or_date",
    "公司或日期": "org_or_date",
    "公司/日期": "org_or_date",
    "causal": "causal",
    "因果": "causal",
    "夸大": "causal",
}
_SPLITS = {"perturbation", "control", "natural"}
_SPLIT_ALIASES = {
    "perturbation": "perturbation",
    "扰动": "perturbation",
    "control": "control",
    "对照": "control",
    "natural": "natural",
    "自然": "natural",
}


def recompute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        method = str(row.get("method") or "")
        profile_id = str(row.get("profile_id") or "")
        grouped[(method, profile_id)].append(row)
    ndcg_values: list[float] = []
    precision_values: list[float] = []
    excluded: list[dict[str, str]] = []
    short_queries: list[dict[str, str]] = []
    per_query: list[dict[str, Any]] = []
    for (method, profile_id), items in sorted(grouped.items()):
        ordered = _ordered(items)
        ratings = [_rating(item) for item in ordered]
        query_ndcg = _ndcg(ratings)
        query_precision = _precision(ratings)
        record = {
            "method": method,
            "profile_id": profile_id,
            "n": len(ordered),
            "ndcg_at_10": query_ndcg,
            "p_at_10": query_precision,
        }
        per_query.append(record)
        if len(ordered) < AT_K:
            short_queries.append({"method": method, "profile_id": profile_id, "n": str(len(ordered))})
        if query_ndcg is None:
            excluded.append({"method": method, "profile_id": profile_id})
        else:
            ndcg_values.append(query_ndcg)
        if query_precision is not None:
            precision_values.append(query_precision)
    labeled = [row for row in rows if row.get("hard_label") in {"pass", "fail", "unclear"}]
    fail_rows = [row for row in labeled if row.get("hard_label") == "fail"]
    pass_rows = [row for row in labeled if row.get("hard_label") == "pass"]
    judged = [row for row in rows if row.get("hard_result") in {"pass", "fail", "needs_review"}]
    return {
        "ndcg_at_10": {
            "macro_mean": _mean(ndcg_values),
            "excluded_no_relevant": excluded,
            "excluded_count": len(excluded),
            "per_query": per_query,
        },
        "p_at_10": {
            "macro_mean": _mean(precision_values),
            "short_queries": short_queries,
            "short_query_count": len(short_queries),
        },
        "hard_false_pass_rate": _rate(fail_rows, "pass"),
        "hard_false_fail_rate": _rate(pass_rows, "fail"),
        "needs_review_rate": (
            None
            if not judged
            else sum(1 for row in judged if row.get("hard_result") == "needs_review") / len(judged)
        ),
        "n_rows": len(rows),
        "note": "指标只由逐条结果重算。本脚本不向 eval/results 写入数字。",
    }


def load_jsonl(
    path: Path,
    *,
    empty_message: str = "逐条结果是空的，没有可重算的指标。",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是 JSON。") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"第 {line_number} 行不是对象。")
        rows.append(payload)
    if not rows:
        raise ValueError(empty_message)
    return rows


def default_input(experiment: str, filename: str) -> Path:
    return REPO_ROOT / "eval" / "results" / experiment / filename


def max_numeric_diff(left: Any, right: Any) -> float:
    """比较两份指标里的数字。结构不一致时抛出 ValueError。"""
    if isinstance(left, bool) or isinstance(right, bool):
        if left != right:
            raise ValueError("布尔字段不一致。")
        return 0.0
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right))
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            raise ValueError("指标字段不一致。")
        peak = 0.0
        for key in left:
            peak = max(peak, max_numeric_diff(left[key], right[key]))
        return peak
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            raise ValueError("指标列表长度不一致。")
        peak = 0.0
        for item, other in zip(left, right):
            peak = max(peak, max_numeric_diff(item, other))
        return peak
    if left != right:
        raise ValueError("指标里的非数字字段不一致。")
    return 0.0


def dcg(ratings: list[float]) -> float:
    total = 0.0
    for index, rating in enumerate(ratings, start=1):
        total += (2.0**rating - 1.0) / math.log2(index + 1)
    return total


def count_e0(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按模式统计采集日志。捕获成功率的分母是被点开的条数。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows, start=1):
        mode = _MODE_NAMES.get(str(row.get("mode") or ""))
        if mode is None:
            raise ValueError(f"第 {index} 行缺少可识别的采集模式（A/B 或 assist/auto）。")
        if not isinstance(row.get("clicked"), bool) or not isinstance(row.get("captured"), bool):
            raise ValueError(f"第 {index} 行的 clicked、captured 必须是布尔值。")
        if not isinstance(row.get("parsed_ok"), bool):
            raise ValueError(f"第 {index} 行的 parsed_ok 必须是布尔值。")
        grouped[mode].append(row)
    by_mode = {mode: _count_e0_mode(grouped[mode]) for mode in sorted(grouped)}
    return {
        "by_mode": by_mode,
        "n_rows": len(rows),
        "note": "计数只来自会话日志。模式 A 与 B 分开，不合并成一个成功率。没有耗时就不填数字。",
    }


def count_e2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """事实精确率、估计召回、引用有效率、JD 类型准确率、hard 误标数。"""
    facts: list[dict[str, Any]] = []
    missed = 0
    jd_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        record_type = str(row.get("record_type") or "")
        if record_type == "fact":
            label = _FACT_LABEL_ALIASES.get(str(row.get("label") or ""))
            if label is None:
                raise ValueError(f"第 {index} 行事实标签必须是正确、部分、错误或幻觉。")
            item = dict(row)
            item["label"] = label
            facts.append(item)
        elif record_type == "missed":
            missed += 1
        elif record_type == "jd_requirement":
            jd_rows.append(row)
        else:
            raise ValueError(f"第 {index} 行 record_type 无法计入 E2。")
    judged = len(facts)
    correct = sum(1 for row in facts if row["label"] == "correct")
    citations = [row for row in facts if isinstance(row.get("citation_valid"), bool)]
    type_rows = [row for row in jd_rows if isinstance(row.get("type_correct"), bool)]
    recall_denominator = correct + missed
    has_fact_audit = judged > 0 or missed > 0
    return {
        "fact_precision": None if judged == 0 else correct / judged,
        "n_facts": judged,
        "n_correct": correct,
        "fact_recall_estimate": None if not has_fact_audit else _divide(correct, recall_denominator),
        "n_missed": None if not has_fact_audit else missed,
        "citation_valid_rate": (
            None
            if not citations
            else sum(1 for row in citations if row["citation_valid"]) / len(citations)
        ),
        "n_citations": len(citations),
        "jd_type_accuracy": (
            None if not type_rows else sum(1 for row in type_rows if row["type_correct"]) / len(type_rows)
        ),
        "n_jd_requirements": len(jd_rows),
        "hard_mislabel_count": (
            None if not jd_rows else sum(1 for row in jd_rows if row.get("inferred_marked_hard") is True)
        ),
        "note": "精确率分母含部分、错误和幻觉。估计召回 = 正确 / (正确 + 漏抽行)。没有对应行的指标为 null。",
    }


def count_e3(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按条件计算无依据陈述率。无法解析的样本进入分子和分母。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows, start=1):
        condition = row.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError(f"第 {index} 行缺少 condition。")
        grouped[condition].append(row)
    by_condition = {name: _count_e3_condition(items) for name, items in sorted(grouped.items())}
    return {
        "by_condition": by_condition,
        "n_rows": len(rows),
        "note": "无依据陈述率只来自审计行。没有盲评、修改标记、调用或耗时就不填这些数字。",
    }


def count_e4(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """描述两组耗时和决策是否相同。缺耗时则为 null。"""
    by_arm_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    profiles: set[str] = set()
    for index, row in enumerate(rows, start=1):
        arm = _ARMS.get(str(row.get("arm") or ""))
        if arm is None:
            raise ValueError(f"第 {index} 行 arm 必须是人工或系统。")
        job_id = str(row.get("job_id") or "")
        profile_id = str(row.get("profile_id") or "")
        if not job_id or not profile_id:
            raise ValueError(f"第 {index} 行缺少 profile_id 或 job_id。")
        decision = row.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError(f"第 {index} 行缺少 decision。")
        key = (profile_id, job_id)
        if arm in decisions[key]:
            raise ValueError(f"第 {index} 行与已有的 {arm} 决策重复。")
        decisions[key][arm] = decision
        by_arm_rows[arm].append(row)
        profiles.add(profile_id)
    paired = [item for item in decisions.values() if "manual" in item and "system" in item]
    agreed = sum(1 for item in paired if item["manual"] == item["system"])
    return {
        "by_arm": {arm: _elapsed_summary(by_arm_rows[arm]) for arm in sorted(by_arm_rows)},
        "decision_agreement_rate": None if not paired else agreed / len(paired),
        "n_paired_jobs": len(paired),
        "n_profiles": len(profiles),
        "note": "只描述输入里的耗时和决策是否相同。人数是输入中的 profile 个数。没有耗时则为 null。",
    }


def count_e5(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """以“存在无依据陈述”为正类，分方法和扰动类型计算 P/R/F1。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        item = _normalize_e5_row(row, index)
        normalized.append(item)
        grouped[item["method"]].append(item)
    by_method = {method: _count_e5_method(items) for method, items in sorted(grouped.items())}
    return {
        "by_method": by_method,
        "n_rows": len(normalized),
        "note": "正类是存在无依据陈述。对照集误报率只统计对照里黄金标签为有依据的行。",
    }


def binary_rates(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    true_positive = sum(1 for gold, predicted in pairs if gold and predicted)
    false_positive = sum(1 for gold, predicted in pairs if not gold and predicted)
    false_negative = sum(1 for gold, predicted in pairs if gold and not predicted)
    true_negative = sum(1 for gold, predicted in pairs if not gold and not predicted)
    assert true_positive + false_positive + false_negative + true_negative == len(pairs)
    precision = _divide(true_positive, true_positive + false_positive)
    recall = _divide(true_positive, true_positive + false_negative)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "tn": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n": len(pairs),
    }


def run_input_cli(
    argv: list[str] | None,
    *,
    description: str,
    flag: str,
    default: Path,
    compute: Callable[[list[dict[str, Any]]], dict[str, Any]],
    missing_noun: str,
    empty_message: str,
) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(flag, default=str(default), help=f"已有的{missing_noun}")
    args = parser.parse_args(argv)
    path = Path(getattr(args, flag.lstrip("-").replace("-", "_")))
    if not path.is_file():
        print(
            json.dumps(_missing_payload(path, missing_noun), ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    try:
        payload = dict(compute(load_jsonl(path, empty_message=empty_message)))
    except ValueError as exc:
        print(
            json.dumps({"error": str(exc), "status": "failed", "wrote_results": False}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    payload["status"] = "recomputed"
    payload["wrote_results"] = False
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _missing_payload(path: Path, missing_noun: str) -> dict[str, Any]:
    return {
        "error": f"找不到{missing_noun}：{path}",
        "missing": [str(path)],
        "status": "not_run",
        "wrote_results": False,
    }


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[bool, int, float, str]:
        rank = item.get("rank")
        missing_rank = rank is None
        score = item.get("score")
        numeric = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else 0.0
        return (missing_rank, int(rank or 10**9), -numeric, str(item.get("snapshot_id") or ""))

    return sorted(items, key=key)


def _rating(item: dict[str, Any]) -> float:
    rating = item.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        return 0.0
    return float(rating)


def _ndcg(ratings: list[float]) -> float | None:
    if not ratings or all(rating < RELEVANCE_MIN for rating in ratings):
        return None
    limit = min(AT_K, len(ratings))
    assert limit == min(AT_K, len(ratings))
    actual = dcg(ratings[:limit])
    ideal = dcg(sorted(ratings, reverse=True)[:limit])
    if ideal == 0:
        return None
    return actual / ideal


def _precision(ratings: list[float]) -> float | None:
    if not ratings:
        return None
    limit = min(AT_K, len(ratings))
    assert limit == min(AT_K, len(ratings))
    hits = sum(1 for rating in ratings[:limit] if rating >= RELEVANCE_MIN)
    return hits / limit


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(rows: list[dict[str, Any]], hard_result: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row.get("hard_result") == hard_result) / len(rows)


def _divide(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _count_e0_mode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clicked = [row for row in rows if row["clicked"]]
    parsed_clicked = sum(1 for row in clicked if row["parsed_ok"])
    parsed_rows = [row for row in rows if row["parsed_ok"]]
    field_names: set[str] = set()
    for row in parsed_rows:
        fields = row.get("fields")
        if isinstance(fields, dict):
            field_names.update(str(name) for name in fields)
    field_rates: dict[str, float] | None = None
    if field_names:
        field_rates = {}
        for name in sorted(field_names):
            present = 0
            for row in parsed_rows:
                fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
                present += int(_field_present(fields.get(name)))
            field_rates[name] = present / len(parsed_rows)
    salary_known = [row["salary_readable"] for row in rows if isinstance(row.get("salary_readable"), bool)]
    elapsed = [_number(row.get("elapsed_ms")) for row in rows]
    elapsed_values = [value for value in elapsed if value is not None]
    stop_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        reason = row.get("stop_reason")
        if isinstance(reason, str) and reason.strip():
            stop_counts[reason] += 1
    return {
        "n_rows": len(rows),
        "n_clicked": len(clicked),
        "n_captured": sum(1 for row in rows if row["captured"]),
        "n_parsed_ok": sum(1 for row in rows if row["parsed_ok"]),
        "capture_success_rate": _divide(parsed_clicked, len(clicked)),
        "field_nonempty_rate": field_rates,
        "salary_readable_rate": (
            None if not salary_known else sum(1 for value in salary_known if value) / len(salary_known)
        ),
        "n_salary_known": len(salary_known),
        "stop_reason_counts": dict(sorted(stop_counts.items())),
        "n_stop_events": sum(stop_counts.values()),
        "mean_elapsed_ms": _mean(elapsed_values),
        "n_elapsed": len(elapsed_values),
    }


def _field_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _count_e3_condition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    factual = 0
    unsupported = 0
    unparseable = 0
    important = 0
    covered = 0
    blind_scores: list[float] = []
    edit_flags: list[bool] = []
    calls: list[float] = []
    elapsed: list[float] = []
    costs: list[float] = []
    for row in rows:
        record_type = row.get("record_type")
        if record_type not in {"claim", "requirement", "review", "run"}:
            raise ValueError("E3 行必须带 record_type：claim、requirement、review 或 run。")
        if record_type == "claim":
            if row.get("unparseable") is True:
                unparseable += 1
                continue
            if row.get("factual") is True:
                if not isinstance(row.get("supported"), bool):
                    raise ValueError("事实性陈述必须给出 supported。")
                factual += 1
                if row["supported"] is False:
                    unsupported += 1
        elif record_type == "requirement":
            if row.get("important") is True:
                if not isinstance(row.get("covered"), bool):
                    raise ValueError("重要要求必须给出 covered。")
                important += 1
                if row["covered"] is True:
                    covered += 1
        elif record_type == "review":
            score = _number(row.get("blind_score"))
            if score is not None:
                blind_scores.append(score)
            if isinstance(row.get("edited"), bool):
                edit_flags.append(row["edited"])
            _collect_optional_numbers(row, calls, elapsed, costs)
        else:
            _collect_optional_numbers(row, calls, elapsed, costs)
    denominator = factual + unparseable
    return {
        "unsupported_statement_rate": _divide(unsupported + unparseable, denominator),
        "n_factual": factual,
        "n_unsupported": unsupported,
        "n_unparseable": unparseable,
        "important_requirement_coverage": _divide(covered, important),
        "n_important_requirements": important,
        "mean_blind_score": _mean(blind_scores),
        "n_blind_scores": len(blind_scores),
        "edit_rate": None if not edit_flags else sum(1 for edited in edit_flags if edited) / len(edit_flags),
        "n_calls": None if not calls else sum(calls),
        "mean_elapsed_ms": _mean(elapsed),
        "cost_usd": None if not costs else sum(costs),
    }


def _collect_optional_numbers(
    row: dict[str, Any],
    calls: list[float],
    elapsed: list[float],
    costs: list[float],
) -> None:
    call_value = _number(row.get("calls"))
    elapsed_value = _number(row.get("elapsed_ms"))
    cost_value = _number(row.get("cost_usd"))
    if call_value is not None:
        calls.append(call_value)
    if elapsed_value is not None:
        elapsed.append(elapsed_value)
    if cost_value is not None:
        costs.append(cost_value)


def _elapsed_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [value for value in (_number(row.get("elapsed_ms")) for row in rows) if value is not None]
    return {"n": len(rows), "mean_elapsed_ms": _mean(values), "n_elapsed": len(values)}


def _normalize_e5_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(row.get("gold_unsupported"), bool) or not isinstance(row.get("predicted_unsupported"), bool):
        raise ValueError(f"第 {index} 行的 gold_unsupported 和 predicted_unsupported 必须是布尔值。")
    split = _SPLIT_ALIASES.get(str(row.get("split") or ""))
    if split is None or split not in _SPLITS:
        raise ValueError(f"第 {index} 行 split 必须是扰动、对照或自然错误。")
    kind = None
    if split == "perturbation":
        kind = _KIND_ALIASES.get(str(row.get("kind") or ""))
        if kind is None:
            raise ValueError(f"第 {index} 行扰动缺少可识别的类型。")
    method = row.get("method")
    method_name = "unspecified" if not isinstance(method, str) or not method.strip() else method
    return {
        "split": split,
        "kind": kind,
        "method": method_name,
        "gold_unsupported": row["gold_unsupported"],
        "predicted_unsupported": row["predicted_unsupported"],
    }


def _count_e5_method(rows: list[dict[str, Any]]) -> dict[str, Any]:
    perturbation = [row for row in rows if row["split"] == "perturbation"]
    control = [row for row in rows if row["split"] == "control"]
    natural = [row for row in rows if row["split"] == "natural"]
    by_kind: dict[str, dict[str, Any]] = {}
    kinds = sorted({str(row["kind"]) for row in perturbation})
    for kind in kinds:
        selected = [row for row in perturbation if row["kind"] == kind]
        by_kind[kind] = binary_rates(_pairs(selected))
    control_negatives = [row for row in control if row["gold_unsupported"] is False]
    false_alarms = sum(1 for row in control_negatives if row["predicted_unsupported"])
    return {
        "perturbation": binary_rates(_pairs(perturbation)),
        "by_kind": by_kind,
        "control_false_positive_rate": _divide(false_alarms, len(control_negatives)),
        "n_control_negatives": len(control_negatives),
        "natural": None if not natural else binary_rates(_pairs(natural)),
    }


def _pairs(rows: list[dict[str, Any]]) -> list[tuple[bool, bool]]:
    return [(row["gold_unsupported"], row["predicted_unsupported"]) for row in rows]
