from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class HistoricalSnapshot:
    snapshot_id: str
    date_range: str
    source: str
    version: str
    pollution_status: str
    created_time: str
    matches: list[dict] = field(default_factory=list)
    excluded_matches: list[dict] = field(default_factory=list)
    pollution_audit: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)
