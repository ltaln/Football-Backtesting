def _score(value: str) -> tuple[int, int]:
    home, away = value.split("-", 1)
    return int(home), int(away)


def _direction(score: tuple[int, int]) -> str:
    return "H" if score[0] > score[1] else "A" if score[0] < score[1] else "D"


def evaluate_score(score_top2: list[str], final_score: str) -> dict[str, bool]:
    if not score_top2:
        return {"evaluable": False, "exact_hit": False, "near_hit": False, "direction_hit": False}
    actual = _score(final_score)
    predicted = [_score(value) for value in score_top2[:2]]
    exact = actual in predicted
    near = not exact and min(abs(p[0] - actual[0]) + abs(p[1] - actual[1]) for p in predicted) == 1
    return {"evaluable": True, "exact_hit": exact, "near_hit": near, "direction_hit": _direction(predicted[0]) == _direction(actual)}
