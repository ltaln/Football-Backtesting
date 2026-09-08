import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn("validation_method", report["improvement_plan"]["proposals"][0])
        self.assertIn("否决标准", report["report_markdown"])
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

    def test_9_replay_range_creates_one_fresh_task_per_day(self):
        from api.backtest_api import ReplayTaskRequest, create_replay_task

        calls = []

        def fake_prediction_request(method, path, body=None):
            calls.append((method, path, body))
            replay_date = body["command"].split()[1]
            return {
                "task_id": f"task-{replay_date}",
                "status_url": f"/v1/tasks/task-{replay_date}",
                "report_url": f"/v1/tasks/task-{replay_date}/report",
                "created": True,
                "must_continue": True,
                "next_operation": "getTaskStatus",
                "instruction": "continue",
                "prompt_bundle": {"prompt_id": "HH520-PROMPT-V2.1"},
            }

        with patch("api.backtest_api._prediction_request", side_effect=fake_prediction_request), \
             patch("api.backtest_api._activate_latest_replay", return_value=("range-test", ["old-task"])):
            response = create_replay_task(ReplayTaskRequest(
                request_id="mobile-range-unique",
                command="回测 2026-07-16 至 2026-07-21",
            ))

        self.assertEqual(response["execution_status"], "CREATED")
        self.assertEqual(response["day_count"], 6)
        self.assertEqual(response["created_count"], 6)
        self.assertEqual(response["failed_count"], 0)
        self.assertEqual(response["next_operation"], "getReplayRangeBundle")
        self.assertEqual(response["cancelled_previous_task_ids"], ["old-task"])
        self.assertEqual([item[2]["command"] for item in calls], [
            "回测 2026-07-16 全部比赛",
            "回测 2026-07-17 全部比赛",
            "回测 2026-07-18 全部比赛",
            "回测 2026-07-19 全部比赛",
            "回测 2026-07-20 全部比赛",
            "回测 2026-07-21 全部比赛",
        ])
        child_request_ids = [item[2]["request_id"] for item in calls]
        self.assertEqual(len(set(child_request_ids)), 6)
        self.assertTrue(all(value != "mobile-range-unique" for value in child_request_ids))
        self.assertEqual([task["task_id"] for task in response["tasks"]], [
            "task-2026-07-16", "task-2026-07-17", "task-2026-07-18",
            "task-2026-07-19", "task-2026-07-20", "task-2026-07-21",
        ])
        self.assertTrue(all(task["prediction_prompt_bundle"] is None for task in response["tasks"]))

    def test_10_replay_range_returns_real_partial_failure_context(self):
        from fastapi import HTTPException
        from api.backtest_api import ReplayTaskRequest, create_replay_task

        def fake_prediction_request(method, path, body=None):
            if path.endswith("/cancel"):
                return {"status": "CANCELLED"}
            if "2026-07-18" in body["command"]:
                raise HTTPException(status_code=503, detail="RESULT_MASK_FAILED")
            replay_date = body["command"].split()[1]
            return {
                "task_id": f"task-{replay_date}",
                "status_url": f"/v1/tasks/task-{replay_date}",
                "report_url": f"/v1/tasks/task-{replay_date}/report",
                "created": True,
                "must_continue": True,
                "next_operation": "getTaskStatus",
                "instruction": "continue",
            }

        with patch("api.backtest_api._prediction_request", side_effect=fake_prediction_request), \
             patch("api.backtest_api._activate_latest_replay"):
            response = create_replay_task(ReplayTaskRequest(
                request_id="mobile-range-partial",
                command="回测 2026-07-16 至 2026-07-19",
            ))

        self.assertEqual(response["execution_status"], "PARTIAL")
        self.assertEqual(response["created_count"], 3)
        self.assertEqual(response["failed_count"], 1)
        self.assertFalse(response["must_continue"])
        self.assertIsNone(response["next_operation"])
        self.assertEqual(response["errors"], [{
            "date": "2026-07-18",
            "request_id": response["errors"][0]["request_id"],
            "command": "回测 2026-07-18 全部比赛",
            "http_status": 503,
            "error": "RESULT_MASK_FAILED",
        }])

    def test_11_replay_status_waits_until_awaiting_gpt(self):
        from api import backtest_api

        responses = iter([
            {"id": "fresh-task", "payload": {}, "status": "COLLECTING", "blockers": [],
             "must_continue": True, "next_operation": "getReplayTask", "instruction": "wait"},
            {"id": "fresh-task", "payload": {}, "status": "AWAITING_GPT", "blockers": [],
             "must_continue": False, "next_operation": "getReplayAnalysisPage", "instruction": "analyze"},
        ])
        with patch.object(backtest_api, "_prediction_request", side_effect=lambda *_: next(responses)), \
             patch.object(backtest_api.time, "sleep"):
            response = backtest_api.get_replay_task("fresh-task")

        self.assertEqual(response["status"], "AWAITING_GPT")
        self.assertEqual(response["next_operation"], "getReplayAnalysisPage")

    def test_12_min_batch_schema_explains_exact_match_identity(self):
        from api.backtest_api import app

        item_schema = app.openapi()["components"]["schemas"]["ReplayPredictionItem"]["properties"]
        self.assertIn("exact match_no", item_schema["n"]["description"])
        self.assertIn("exact code", item_schema["c"]["description"])
        self.assertIn("精准比分 Top3：1-0 / 1-1 / 2-0", item_schema["r"]["description"])
        self.assertIn("Never replace a prediction with a reason", item_schema["r"]["description"])

    def test_13_range_bundle_collapses_all_days_and_ultra_format(self):
        from api import backtest_api

        task_ids = ["a" * 32, "b" * 32]

        def fake_prediction_request(method, path, body=None):
            if path.endswith("/analysis-batch"):
                day = "2026-08-01" if task_ids[0] in path else "2026-08-02"
                return {"prompt_bundle": {"prompt_id": "frozen", "execution_prompt": "FULL FROZEN PROMPT"}, "matches": [{
                    "date": day, "match_no": 1, "code": day[-2:] + "001",
                    "kickoff_at_raw": day + " 20:00", "identity_check": {"home": "H", "away": "A"},
                    "package_sha256": "f" * 64, "result_mask": {"applied": True},
                    "sections": [{"category": "mixed_data", "source_url": "https://example.test",
                                  "content": "赛前证据"}],
                }]}
            task_id = path.rsplit("/", 1)[-1]
            return {"id": task_id, "status": "AWAITING_GPT", "blockers": []}

        with patch.object(backtest_api, "_prediction_request", side_effect=fake_prediction_request):
            response = backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-02", task_ids=task_ids,
            ))

        self.assertTrue(response["ready"])
        self.assertTrue(response["must_continue"])
        self.assertEqual([item["k"] for item in response["matches"]], [1, 2])
        self.assertFalse(response["has_more"])
        self.assertEqual(response["next_operation"], "completeReplayRange")
        self.assertEqual(response["prediction_prompt_bundle"]["execution_prompt"], "FULL FROZEN PROMPT")
        self.assertNotIn("CCCCCCCCCCCCC", response["output_format"])
        key, values, modules = backtest_api._decode_ultra(
            "1|1:0,1:1,2:0|HH,DH,DD|H-0.5|U2.5|H|2-3|62|CCCCCCCCCCCCC"
        )
        self.assertEqual(key, 1)
        self.assertIn("主胜", values[4])
        self.assertEqual(modules, "C" * 13)
        _, pass_values, _ = backtest_api._decode_ultra(
            "2|PASS|P|P|P|P|P|40|DDDDDDDDDDDDD"
        )
        self.assertEqual(pass_values[0], "精准比分 Top3：PASS")
        self.assertEqual(pass_values[1], "半全场 Top3：PASS")
        _, hyphen_values, _ = backtest_api._decode_ultra(
            "3|1-0，1-1，2-0|HH，DH，DD|H-0.5|U2.5|H|2-3|62|CCCCCCCCCCCCC"
        )
        self.assertEqual(hyphen_values[0], "精准比分 Top3：1-0 / 1-1 / 2-0")
        _, slash_values, _ = backtest_api._decode_ultra(
            "4|1-0,1:1,2-1|H/H,D-H,平/胜|H-0.5|O2.5|H|3|61|CCCCCCCCCCCCC"
        )
        self.assertEqual(slash_values[1], "半全场 Top3：胜/胜 / 平/胜 / 平/胜")
        paths = {route.path for route in backtest_api.app.routes}
        self.assertTrue({"/replay/range/bundle", "/replay/range/complete"} <= paths)
        complete_schema = backtest_api.app.openapi()["components"]["schemas"]["ReplayRangeCompleteRequest"]
        self.assertEqual(complete_schema["properties"]["p"]["maxItems"], 12)
        report_schema = backtest_api.app.openapi()["components"]["schemas"]["ReplayRangeReportPageResponse"]
        self.assertIn("must_continue", report_schema["required"])

    def test_14_missing_score_evidence_forces_correct_score_degradation(self):
        from api.backtest_api import _evidence_audit

        mask, refs = _evidence_audit({
            "identity_check": {"result": "PASS"}, "result_mask": {"applied": True},
            "sections": [
                {"category": "mixed_data", "source_url": "mixed", "content": "Home VS Away"},
                {"category": "asian_handicap_changes", "source_url": "asian", "content": "初盘主 2.0 初盘平 3.0 初盘客 4.0"},
                {"category": "score_odds_changes", "source_url": "score", "content": "模块降级"},
            ],
        })
        self.assertEqual(len(mask), 13)
        self.assertEqual(mask[6], "D")
        self.assertEqual(refs["correct_score"], ["score"])

    def test_15_unknown_error_is_not_called_random_event(self):
        from analysis.error_analyzer import ErrorAnalyzer

        evaluation = {key: {"evaluable": True} for key in ("score", "htft", "result", "goal")}
        evaluation["score"]["exact_hit"] = False
        evaluation["htft"]["overall_hit"] = True
        evaluation["result"].update(hit=False)
        evaluation["goal"]["range"] = True
        match = {"prediction": {"result": {"confidence": 0.5}}, "error_signals": {}}
        self.assertEqual(ErrorAnalyzer().classify(match, evaluation), "B_MODEL_JUDGMENT_ERROR")
        match["error_signals"] = {"information_insufficient": True}
        self.assertEqual(ErrorAnalyzer().classify(match, evaluation), "E_INFORMATION_INSUFFICIENT")

    def test_16_range_evidence_is_bounded_and_oversized_report_is_paginated(self):
        from api import backtest_api

        task_ids = ["a" * 32, "b" * 32]

        def fake_prediction_request(method, path, body=None):
            if path.endswith("/analysis-batch"):
                day = "2026-08-01" if task_ids[0] in path else "2026-08-02"
                return {"prompt_bundle": {"execution_prompt": "FULL"}, "matches": [{
                    "date": day, "match_no": n, "code": f"{day[-2:]}00{n}",
                    "result_mask": {"applied": True}, "identity_check": {"result": "PASS"},
                    "sections": [{"category": "mixed_data", "source_url": "ref", "content": "赛前证据"}],
                } for n in (1, 2)]}
            return {"id": path.rsplit("/", 1)[-1], "status": "AWAITING_GPT", "blockers": []}

        with patch.object(backtest_api, "_prediction_request", side_effect=fake_prediction_request):
            first = backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-02", task_ids=task_ids, cursor=0))
            second = backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-02", task_ids=task_ids, cursor=3))
        self.assertEqual([item["k"] for item in first["matches"]], [1, 2, 3, 4])
        self.assertIsNone(first["next_cursor"])
        self.assertFalse(first["has_more"])
        self.assertEqual([item["k"] for item in second["matches"]], [4])
        self.assertIsNone(second["prediction_prompt_bundle"])

        class FakeManager:
            @staticmethod
            def report(task_id):
                return {"report_markdown": "段落\n" * 50000}

        with patch.object(backtest_api, "get_manager", return_value=FakeManager()):
            page = backtest_api.get_replay_range_report_page("BT-1", cursor=0)
        self.assertTrue(page["has_more"])
        self.assertTrue(page["must_continue"])
        self.assertLessEqual(len(page["content"]), backtest_api.REPORT_PAGE_CHARS)
        self.assertEqual(page["next_operation"], "getReplayRangeReportPage")

    def test_17_backtest_status_falls_back_to_persisted_replay_task(self):
        from api import backtest_api

        class FakeManager:
            @staticmethod
            def status(task_id):
                return None

        replay = {"id": "4" * 32, "payload": {"date": "2026-07-22"},
                  "status": "AWAITING_GPT", "created": 1, "blockers": []}
        with patch.object(backtest_api, "get_manager", return_value=FakeManager()), \
                patch.object(backtest_api, "_prediction_request", return_value=replay):
            result = backtest_api.get_status("4" * 32)
        self.assertTrue(result["replay_mode"])
        self.assertEqual(result["status"], "AWAITING_GPT")
        self.assertEqual(result["next_operation"], "getReplayRangeBundle")

    def test_18_new_replay_run_supersedes_previous_group(self):
        from database.db import Database

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Database(Path(temporary.name) / "db.sqlite3")
        self.assertEqual(database.activate_replay_run("range-1", "request-1", "first", ["a", "b"]), [])
        self.assertEqual(database.activate_replay_run("range-2", "request-2", "second", ["c"]), ["a", "b"])
        self.assertEqual(database.activate_replay_run("range-2", "request-2", "second", ["c"]), [])
        database.complete_replay_run(["c"])
        self.assertEqual(database.activate_replay_run("range-3", "request-3", "third", ["d"]), [])

    def test_19_partial_range_task_fails_with_real_blocker(self):
        from fastapi import HTTPException
        from api import backtest_api

        task_ids = ["a" * 32, "b" * 32]

        def fake_prediction_request(method, path, body=None):
            task_id = path.rsplit("/", 1)[-1]
            status = "PARTIAL" if task_id == task_ids[0] else "AWAITING_GPT"
            return {"id": task_id, "status": status,
                    "blockers": ["COLLECTION_INCOMPLETE"] if status == "PARTIAL" else []}

        with patch.object(backtest_api, "_prediction_request", side_effect=fake_prediction_request), \
                self.assertRaises(HTTPException) as raised:
            backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-02", task_ids=task_ids))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["tasks"][0]["status"], "PARTIAL")

    def test_20_range_bundle_streams_stable_ready_date_prefix(self):
        from api import backtest_api

        task_ids = ["a" * 32, "b" * 32, "c" * 32]

        def fake_prediction_request(method, path, body=None):
            task_id = next((value for value in task_ids if value in path), None)
            if path.endswith("/analysis-batch"):
                self.assertIn(task_id, task_ids[:2])
                return {"prompt_bundle": {"execution_prompt": "FULL"}, "matches": [{
                    "date": "2026-08-0" + str(task_ids.index(task_id) + 1),
                    "match_no": 1, "code": "001", "result_mask": {"applied": True},
                    "identity_check": {"result": "PASS"}, "sections": [],
                }]}
            status = "COLLECTING" if task_id == task_ids[2] else "AWAITING_GPT"
            return {"id": task_id, "status": status, "blockers": []}

        with patch.object(backtest_api, "_prediction_request", side_effect=fake_prediction_request):
            page = backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-03", task_ids=task_ids, cursor=0))
            waiting = backtest_api.get_replay_range_bundle(backtest_api.ReplayRangeRequest(
                command="回测 2026-08-01 至 2026-08-03", task_ids=task_ids, cursor=2))

        self.assertTrue(page["ready"])
        self.assertEqual([item["k"] for item in page["matches"]], [1, 2])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_cursor"], 2)
        self.assertFalse(waiting["ready"])
        self.assertEqual(waiting["pending_task_ids"], [task_ids[2]])


if __name__ == "__main__":
    unittest.main()
