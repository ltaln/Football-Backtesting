def evaluate_htft(prediction: dict, actual: dict) -> dict[str, bool]:
    half_hit = prediction.get("half_result") == actual.get("half_result")
    transition_hit = prediction.get("full_result") == actual.get("full_result")
    return {"half_hit": half_hit, "transition_hit": transition_hit, "overall_hit": half_hit and transition_hit}
