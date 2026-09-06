import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from collector.snapshot_checker import check_match_record, check_snapshot
from snapshot.snapshot_model import HistoricalSnapshot
from snapshot.time_pollution_filter import sanitize_prediction_input


class SnapshotManager:
    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def freeze(self, candidate: dict, start_date: date, end_date: date) -> HistoricalSnapshot:
        sanitized_matches, audits = [], []
        for match in candidate.get("matches", []):
            check_match_record(match)
            item = dict(match)
            clean_input, audit = sanitize_prediction_input(item.get("prediction_input", {}))
            item["prediction_input"] = clean_input
            sanitized_matches.append(item)
            audits.append({"match_id": item.get("match_id"), **audit})
        status = "SANITIZED" if any(a["removed"] or a["downgraded"] for a in audits) else "CLEAN"
        snapshot = HistoricalSnapshot(
            snapshot_id=f"SNP-{uuid4().hex[:12]}",
            date_range=f"{start_date.isoformat()}/{end_date.isoformat()}",
            source=candidate.get("source", "unknown"),
            version=candidate.get("version", "1.0"),
            pollution_status=status,
            created_time=datetime.now(timezone.utc).isoformat(),
            matches=sanitized_matches,
            pollution_audit=audits,
        )
        payload = snapshot.as_dict()
        check_snapshot(payload)
        path = self.snapshot_dir / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return snapshot
