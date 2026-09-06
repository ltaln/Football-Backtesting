import re
from dataclasses import dataclass
from datetime import date

from core.config import DEFAULT_SETTINGS


class CommandError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BacktestCommand:
    type: str
    start_date: date
    end_date: date

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat()}


_COMMAND = re.compile(r"^\s*回测\s+(\d{4}-\d{2}-\d{2})(?:\s*至\s*(\d{4}-\d{2}-\d{2}))?(?:\s+全部比赛)?\s*$")


def parse_command(text: str) -> BacktestCommand:
    match = _COMMAND.match(text)
    if not match:
        raise CommandError("INVALID_BACKTEST_COMMAND")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2) or match.group(1))
    except ValueError as exc:
        raise CommandError("INVALID_BACKTEST_DATE") from exc
    if end < start:
        raise CommandError("INVALID_BACKTEST_DATE_RANGE")
    if (end - start).days + 1 > DEFAULT_SETTINGS.max_backtest_days:
        raise CommandError("BACKTEST_WINDOW_LIMIT_EXCEEDED")
    return BacktestCommand("BACKTEST", start, end)
