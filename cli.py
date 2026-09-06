import argparse
import json

from core.command_parser import CommandError
from core.task_manager import TaskManager


def main() -> int:
    parser = argparse.ArgumentParser(description="HH520 Insight AI 单命令回测")
    parser.add_argument("command", nargs="+", help="例如：回测 2026-08-01 全部比赛")
    args = parser.parse_args()
    try:
        report = TaskManager().run(" ".join(args.command))
    except (CommandError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
