def evaluate_htft(prediction: dict | list[dict], actual: dict) -> dict[str, bool | int]:
    candidates = prediction if isinstance(prediction, list) else ([prediction] if prediction else [])
    candidates = [item for item in candidates if isinstance(item, dict) and item.get("half_result") and item.get("full_result")]
    target = (actual.get("half_result"), actual.get("final_result"))
    pairs = [(item["half_result"], item["full_result"]) for item in candidates[:3]]
    return {
        "evaluable": bool(pairs),
        "candidate_count": len(pairs),
        "half_hit": any(pair[0] == target[0] for pair in pairs),
        "transition_hit": any(pair[1] == target[1] for pair in pairs),
        "top1_overall_hit": bool(pairs) and pairs[0] == target,
        "overall_hit": target in pairs,
    }
