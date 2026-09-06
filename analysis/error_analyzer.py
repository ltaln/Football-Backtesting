ERROR_TYPES = {"NOT_EVALUABLE", "DATA_ERROR", "MARKET_ERROR", "TEAM_STATE_ERROR", "GAME_FLOW_ERROR", "RANDOM_EVENT"}


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
            return "DATA_ERROR"
        if signals.get("market_error"):
            return "MARKET_ERROR"
        if signals.get("team_state_error"):
            return "TEAM_STATE_ERROR"
        if signals.get("game_flow_error"):
            return "GAME_FLOW_ERROR"
        return "RANDOM_EVENT"
