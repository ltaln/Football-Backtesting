from evaluation.goal_engine import evaluate_goals
from evaluation.htft_engine import evaluate_htft
from evaluation.result_engine import evaluate_result
from evaluation.score_engine import evaluate_score


class EvaluationEngine:
    def evaluate(self, match: dict) -> dict:
        prediction = match.get("prediction", {})
        actual = match.get("actual", {})
        return {
            "score": evaluate_score(prediction.get("score_top2", []), actual.get("final_score", "0-0")),
            "htft": evaluate_htft(prediction.get("htft", {}), actual),
            "result": evaluate_result(prediction.get("result", {}), actual),
            "goal": evaluate_goals(prediction.get("goal", {}), actual),
        }
