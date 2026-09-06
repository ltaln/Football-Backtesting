from dataclasses import asdict, dataclass


@dataclass
class BacktestTask:
    task_id: str
    start_date: str
    end_date: str
    status: str
    snapshot_id: str | None
    created_time: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationRecord:
    match_id: str
    prediction_json: str
    actual_json: str
    evaluation_json: str
    error_type: str | None
