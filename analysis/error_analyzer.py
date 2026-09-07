ERROR_TYPES = {
    "NOT_EVALUABLE", "A_DATA_ERROR", "B_MODEL_JUDGMENT_ERROR", "C_CALIBRATION_ERROR",
    "D_ABNORMAL_MATCH", "E_INFORMATION_INSUFFICIENT",
}


class ErrorAnalyzer:
    def classify(self, match: dict, evaluation: dict) -> str | None:
        sections = (evaluation["score"], evaluation["htft"], evaluation["result"], evaluation["goal"])
        if not any(section.get("evaluable") for section in sections):
            return "NOT_EVALUABLE"
        scored = [
            evaluation["score"]["exact_hit"] if evaluation["score"].get("evaluable") else True,
            evaluation["htft"]["overall_hit"] if evaluation["htft"].get("evaluable") else True,
            evaluation["result"]["hit"] if evaluation["result"].get("evaluable") else True,
            evaluation["goal"]["range"] if evaluation["goal"].get("evaluable") else True,
        ]
        if all(scored):
            return None
        signals = match.get("error_signals", {})
        if signals.get("data_error"):
            return "A_DATA_ERROR"
        if signals.get("abnormal_match") or signals.get("game_flow_error"):
            return "D_ABNORMAL_MATCH"
        if signals.get("information_insufficient"):
            return "E_INFORMATION_INSUFFICIENT"
        confidence = match.get("prediction", {}).get("result", {}).get("confidence")
        if isinstance(confidence, (int, float)) and confidence >= 0.65 and not evaluation["result"].get("hit", True):
            return "C_CALIBRATION_ERROR"
        return "B_MODEL_JUDGMENT_ERROR"
