ERROR_TYPES = {"DATA_ERROR", "MARKET_ERROR", "TEAM_STATE_ERROR", "GAME_FLOW_ERROR", "RANDOM_EVENT"}


class ErrorAnalyzer:
    def classify(self, match: dict, evaluation: dict) -> str | None:
        if all((evaluation["score"]["exact_hit"], evaluation["htft"]["overall_hit"], evaluation["result"]["hit"], evaluation["goal"]["exact"])):
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
