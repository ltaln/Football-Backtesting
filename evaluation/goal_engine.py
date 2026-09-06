def evaluate_goals(prediction: dict, actual: dict) -> dict[str, bool]:
    total = int(actual.get("total_goals", 0))
    expected = prediction.get("exact")
    goal_range = prediction.get("range", [])
    in_range = isinstance(goal_range, list) and len(goal_range) == 2 and int(goal_range[0]) <= total <= int(goal_range[1])
    return {"exact": expected is not None and int(expected) == total, "range": in_range}
