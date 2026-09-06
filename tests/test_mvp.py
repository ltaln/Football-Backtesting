import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

from collector.collector_adapter import CollectorAdapter
from collector.football_ai_adapter import _htft, _result
from core.command_parser import CommandError, parse_command
from core.config import Settings
from core.prompt_loader import load_insight_prompt, prompt_binding
from core.task_manager import TaskManager
from snapshot.time_pollution_filter import sanitize_prediction_input


MATCH = {
    "match_id": "TEST-001", "match_date": "2026-08-01", "match": "示例主队 vs 示例客队",
    "prediction_input": {"prematch_odds": {"H": 2.0}, "final_score": "1-0", "post_match_news": "remove", "post_match_status": "downgrade"},
    "prediction": {"score_top2": ["1-0", "1-1"], "htft": {"half_result": "D", "full_result": "H"}, "result": {"result": "H", "confidence": 0.68}, "goal": {"exact": 1, "range": [1, 2]}},
    "actual": {"final_score": "1-0", "half_result": "D", "final_result": "H", "total_goals": 1},
}


class StaticCollector(CollectorAdapter):
    def collect_history(self, start_date: date, end_date: date) -> dict:
        return {"source": "test", "version": "1.0", "matches": [MATCH]}


class EmptyCollector(CollectorAdapter):
    def collect_history(self, start_date: date, end_date: date) -> dict:
        return {"source": "test", "version": "1.0", "matches": []}


class PartialCollector(CollectorAdapter):
    def collect_history(self, start_date: date, end_date: date) -> dict:
        unavailable = deepcopy(MATCH)
        unavailable["match_id"] = "TEST-002"
        unavailable["prediction"] = {"score_top2": [], "htft": [], "result": {"result": [], "confidence": "unknown"}, "goal": {"exact": None, "range": []}}
        return {"source": "test", "version": "1.2", "matches": [MATCH, unavailable]}


class MVPTests(unittest.TestCase):
    def run_command(self, command: str) -> dict:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log_path = Path(__file__).resolve().parents[1] / "logs" / "test.log"
        settings = Settings(database_path=root / "db.sqlite3", snapshot_dir=root / "snapshots", archive_dir=root / "archive", report_dir=root / "reports", log_path=log_path)
        return TaskManager(settings, StaticCollector()).run(command)

    def test_1_single_day_command_completes(self):
        report = self.run_command("回测 2026-08-01 全部比赛")
        self.assertEqual(report["status"], "REPORT_READY")
        self.assertEqual(report["summary"]["total_matches"], 1)
        self.assertFalse(report["improvement_plan"]["auto_apply"])
        self.assertIn("改进方案", report["report_markdown"])
        from api.backtest_api import app
        paths = {route.path for route in app.routes}
        self.assertTrue({"/backtest/run", "/backtest/status/{task_id}", "/backtest/report/{task_id}"} <= paths)

    def test_2_seven_day_range_succeeds(self):
        report = self.run_command("回测 2026-08-01至2026-08-07")
        self.assertEqual(report["status"], "REPORT_READY")

    def test_3_eight_day_range_is_rejected(self):
        with self.assertRaisesRegex(CommandError, "BACKTEST_WINDOW_LIMIT_EXCEEDED"):
            parse_command("回测 2026-08-01至2026-08-08")

    def test_4_pollution_fields_do_not_enter_snapshot_input(self):
        clean, audit = sanitize_prediction_input(MATCH["prediction_input"])
        self.assertNotIn("final_score", clean)
        self.assertNotIn("post_match_news", clean)
        self.assertIn("post_match_status", clean)
        self.assertIn("post_match_status", audit["downgraded"])

    def test_5_strict_result_and_top3_htft_parsing(self):
        match = "示例主队 vs 示例客队"
        self.assertEqual(_result("胜平负：主胜", match), ["H"])
        self.assertEqual(_result("胜平负：客胜", match), ["A"])
        self.assertEqual(_result("胜平负：平", match), ["D"])
        self.assertEqual(_result("胜平负：示例客队胜（低优势）", match), ["A"])
        self.assertEqual(_result("胜平负：PASS（资料不足）", match), [])
        self.assertEqual(len(_htft("半全场 Top3：胜/胜 / 平/胜 / 平/平")), 3)

    def test_6_every_command_starts_a_fresh_task(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = Settings(database_path=root / "db.sqlite3", snapshot_dir=root / "snapshots", archive_dir=root / "archive", report_dir=root / "reports", log_path=root / "test.log")
        with self.assertRaisesRegex(ValueError, "NO_MATCHES_IN_DATE_RANGE"):
            TaskManager(settings, EmptyCollector()).run("回测 2026-08-01 全部比赛")
        first = TaskManager(settings, StaticCollector()).run("回测 2026-08-02 全部比赛")
        second = TaskManager(settings, StaticCollector()).run("回测 2026-08-02 全部比赛")
        self.assertEqual(first["status"], "REPORT_READY")
        self.assertNotEqual(first["task_id"], second["task_id"])

    def test_7_not_evaluable_predictions_do_not_enter_metric_denominators(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = Settings(database_path=root / "db.sqlite3", snapshot_dir=root / "snapshots", archive_dir=root / "archive", report_dir=root / "reports", log_path=root / "test.log")
        report = TaskManager(settings, PartialCollector()).run("回测 2026-08-01 全部比赛")
        self.assertEqual(report["summary"]["total_matches"], 2)
        self.assertEqual(report["summary"]["valid_matches"], 1)
        self.assertEqual(report["summary"]["metric_samples"]["result"], 1)
        self.assertEqual(report["summary"]["result_accuracy"], 1.0)

    def test_8_insight_prompt_is_loaded_and_bound_to_report(self):
        bundle = load_insight_prompt()
        self.assertEqual(bundle["prompt_id"], "HH520-INSIGHT-AI-V1.0")
        self.assertIn("你现在运行的是 HH520 Insight AI 回测系统。", bundle["content"])
        self.assertEqual(len(bundle["sha256"]), 64)
        report = self.run_command("回测 2026-08-01 全部比赛")
        self.assertEqual(report["insight_prompt_binding"], prompt_binding(bundle))


if __name__ == "__main__":
    unittest.main()
