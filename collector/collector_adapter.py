import json
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path


class CollectorAdapter(ABC):
    @abstractmethod
    def collect_history(self, start_date: date, end_date: date, prediction_commit_ids: list[str] | None = None) -> dict:
        """Return a Historical Snapshot candidate for the requested range."""


class LocalArchiveCollector(CollectorAdapter):
    """Minimal V1 adapter. Reads JSON archives; remote collection plugs in here later."""

    def __init__(self, archive_dir: Path):
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def collect_history(self, start_date: date, end_date: date, prediction_commit_ids: list[str] | None = None) -> dict:
        matches: list[dict] = []
        for path in sorted(self.archive_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload if isinstance(payload, list) else payload.get("matches", [])
            for item in items:
                match_date = date.fromisoformat(item["match_date"])
                if start_date <= match_date <= end_date:
                    matches.append(item)
        return {"source": "local_prediction_archive", "version": "1.0", "matches": matches}
