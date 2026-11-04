"""从抽取审计重算 E2 计数。输入不在时失败，不写 eval/results。"""

from __future__ import annotations

import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from metrics import count_e2, default_input, run_input_cli


def main(argv: list[str] | None = None) -> int:
    return run_input_cli(
        argv,
        description="从抽取审计重算 E2 计数",
        flag="--audit",
        default=default_input("e2", "audit.jsonl"),
        compute=count_e2,
        missing_noun="抽取审计",
        empty_message="抽取审计是空的，没有可计数的行。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
