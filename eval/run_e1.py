"""从逐条结果重算 E1 指标。

不创建 eval/results，也不填写没有逐条结果支撑的数字。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


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
        if len(ordered) < 10:
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
            "per_query": per_query,
        },
        "p_at_10": {
            "macro_mean": _mean(precision_values),
            "short_queries": short_queries,
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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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
        raise ValueError("逐条结果是空的，没有可重算的指标。")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 per-query-results.jsonl 重算 E1 指标")
    parser.add_argument("--per-query", required=True, help="已有的逐条结果 JSONL")
    args = parser.parse_args(argv)
    path = Path(args.per_query)
    if not path.is_file():
        print(
            json.dumps({"error": f"找不到逐条结果：{path}", "wrote_results": False}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    try:
        metrics = recompute_metrics(load_jsonl(path))
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "wrote_results": False}, ensure_ascii=False), file=sys.stderr)
        return 1
    json.dump(metrics, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


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
    if not ratings or all(rating < 2 for rating in ratings):
        return None
    limit = min(10, len(ratings))
    actual = _dcg(ratings[:limit])
    ideal = _dcg(sorted(ratings, reverse=True)[:limit])
    if ideal == 0:
        return None
    return actual / ideal


def _precision(ratings: list[float]) -> float | None:
    if not ratings:
        return None
    limit = min(10, len(ratings))
    hits = sum(1 for rating in ratings[:limit] if rating >= 2)
    return hits / limit


def _dcg(ratings: list[float]) -> float:
    total = 0.0
    for index, rating in enumerate(ratings, start=1):
        total += (2.0**rating - 1.0) / math.log2(index + 1)
    return total


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(rows: list[dict[str, Any]], hard_result: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row.get("hard_result") == hard_result) / len(rows)


if __name__ == "__main__":
    raise SystemExit(main())
