"""从逐条结果重算 E1 指标。

不创建 eval/results，也不填写没有逐条结果支撑的数字。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from metrics import load_jsonl, recompute_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 per-query-results.jsonl 重算 E1 指标")
    parser.add_argument("--per-query", required=True, help="已有的逐条结果 JSONL")
    args = parser.parse_args(argv)
    path = Path(args.per_query)
    if not path.is_file():
        print(
            json.dumps(
                {
                    "error": f"找不到逐条结果：{path}",
                    "missing": [str(path)],
                    "status": "not_run",
                    "wrote_results": False,
                },
                ensure_ascii=False,
            ),
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


if __name__ == "__main__":
    raise SystemExit(main())
