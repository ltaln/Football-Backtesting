REMOVE_FIELDS = {"final_score", "final_result", "post_match_odds", "post_match_news"}
DOWNGRADE_FIELDS = {"post_match_status", "after_match_confirmed_information"}
KEEP_FIELDS = {"prematch_odds", "handicap", "team_form", "injuries_before_match"}


def sanitize_prediction_input(payload: dict) -> tuple[dict, dict]:
    clean = dict(payload)
    removed = sorted(field for field in REMOVE_FIELDS if field in clean)
    downgraded = sorted(field for field in DOWNGRADE_FIELDS if field in clean)
    kept = sorted(field for field in KEEP_FIELDS if field in clean)
    for field in removed:
        clean.pop(field, None)
    return clean, {"removed": removed, "downgraded": downgraded, "kept": kept}
