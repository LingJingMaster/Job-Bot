"""从采集会话日志重算 E0 计数。输入不在时失败，不写 eval/results。"""

from __future__ import annotations

import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from metrics import count_e0, default_input, run_input_cli


def main(argv: list[str] | None = None) -> int:
    return run_input_cli(
        argv,
        description="从采集会话日志重算 E0 计数",
        flag="--session-log",
        default=default_input("e0", "session-log.jsonl"),
        compute=count_e0,
        missing_noun="采集会话日志",
        empty_message="采集会话日志是空的，没有可计数的行。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
