REQUIRED_FIELDS = {"snapshot_id", "date_range", "source", "version", "pollution_status", "created_time"}
MATCH_FIELDS = {"match_id", "match_date", "prediction", "actual"}


def check_snapshot(snapshot: dict) -> None:
    missing = REQUIRED_FIELDS - snapshot.keys()
    if missing:
        raise ValueError(f"SNAPSHOT_FIELDS_MISSING:{','.join(sorted(missing))}")
    if snapshot["pollution_status"] not in {"CLEAN", "SANITIZED"}:
        raise ValueError("SNAPSHOT_POLLUTION_CHECK_FAILED")


def check_match_record(match: dict) -> None:
    missing = MATCH_FIELDS - match.keys()
    if missing:
        raise ValueError(f"MATCH_FIELDS_MISSING:{','.join(sorted(missing))}")
    prediction, actual = match["prediction"], match["actual"]
    for section in ("score_top2", "htft", "result", "goal"):
        if section not in prediction:
            raise ValueError(f"PREDICTION_SECTION_MISSING:{section}")
    for field in ("final_score", "half_result", "final_result", "total_goals"):
        if field not in actual:
            raise ValueError(f"ACTUAL_FIELD_MISSING:{field}")
