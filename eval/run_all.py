"""检查 E0–E5 与 E1a 的逐条输入。缺输入时说明 not_run，不创建 eval/results。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from metrics import (
    count_e0,
    count_e2,
    count_e3,
    count_e4,
    count_e5,
    default_input,
    load_jsonl,
    recompute_metrics,
)

_EXPERIMENTS: tuple[tuple[str, str, str, str, Callable[[list[dict[str, Any]]], dict[str, Any]], str], ...] = (
    ("E0", "session_log", "e0", "session-log.jsonl", count_e0, "采集会话日志是空的，没有可计数的行。"),
    ("E1", "e1", "e1", "per-query-results.jsonl", recompute_metrics, "逐条结果是空的，没有可重算的指标。"),
    ("E1a", "e1a", "e1a", "per-query-results.jsonl", recompute_metrics, "逐条结果是空的，没有可重算的指标。"),
    ("E2", "e2", "e2", "audit.jsonl", count_e2, "抽取审计是空的，没有可计数的行。"),
    ("E3", "e3", "e3", "audit.jsonl", count_e3, "材料审计是空的，没有可计数的行。"),
    ("E4", "e4", "e4", "sessions.jsonl", count_e4, "效率记录是空的，没有可计数的行。"),
    ("E5", "e5", "e5", "judgments.jsonl", count_e5, "校验器判断是空的，没有可计数的行。"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重算已有实验输入；缺输入则记 not_run")
    for experiment_id, flag, directory, filename, _compute, _empty in _EXPERIMENTS:
        parser.add_argument(
            f"--{flag.replace('_', '-')}",
            default=str(default_input(directory, filename)),
            help=f"{experiment_id} 的已有输入",
        )
    args = parser.parse_args(argv)
    experiments: dict[str, dict[str, Any]] = {}
    any_missing = False
    for experiment_id, flag, _directory, _filename, compute, empty_message in _EXPERIMENTS:
        path = Path(getattr(args, flag))
        if not path.is_file():
            any_missing = True
            experiments[experiment_id] = {
                "status": "not_run",
                "missing": [str(path)],
                "reason": f"找不到输入：{path}",
            }
            continue
        try:
            metrics = compute(load_jsonl(path, empty_message=empty_message))
        except ValueError as exc:
            any_missing = True
            experiments[experiment_id] = {
                "status": "failed",
                "missing": [str(path)],
                "reason": str(exc),
            }
            continue
        experiments[experiment_id] = {"status": "recomputed", "missing": [], "metrics": metrics}
    report = {"wrote_results": False, "experiments": experiments}
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if any_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
