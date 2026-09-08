import tempfile
import unittest
from datetime import date
from pathlib import Path

from collector.collector_adapter import CollectorAdapter
from core.config import Settings
from core.task_manager import TaskManager


MATCH = {
    "match_id": "FACT-001",
    "match_date": "2026-08-01",
    "match": "事实主队 vs 事实客队",
    "prediction_input": {
        "model_version": "HH520-V2.1-Test",
        "prompt_version": "HH520-PROMPT-V2.1",
        "source_warnings": ["lineup degraded"],
    },
    "prediction": {
        "score_top2": ["1-0", "1-1"],
        "htft": [{"half_result": "D", "full_result": "H"}],
        "result": {"result": ["H"], "confidence": 0.72},
        "goal": {"exact": 1, "range": [1, 2]},
    },
    "actual": {
        "final_score": "1-0",
        "half_result": "D",
        "final_result": "H",
        "total_goals": 1,
    },
    "modules": [{
        "module_id": "team_analysis",
        "status": "DEGRADED",
        "evidence_refs": ["lineup:test"],
    }],
    "source_warnings": ["lineup degraded"],
    "error_signals": {"information_insufficient": True},
}


class StaticCollector(CollectorAdapter):
    def collect_history(self, start_date: date, end_date: date, prediction_commit_ids=None) -> dict:
        return {"source": "test", "version": "1.0", "matches": [MATCH]}


class GPTFinalAnalysisTests(unittest.TestCase):
    def test_final_report_is_full_fact_package_for_gpt(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = Settings(
            database_path=root / "db.sqlite3",
            snapshot_dir=root / "snapshots",
            archive_dir=root / "archive",
            report_dir=root / "reports",
            log_path=root / "test.log",
        )
        report = TaskManager(settings, StaticCollector()).run("回测 2026-08-01 全部比赛")
        markdown = report["report_markdown"]

        self.assertEqual(report["improvement_plan"]["analysis_owner"], "GPT")
        self.assertTrue(report["improvement_plan"]["server_recommendations_disabled"])
        self.assertIn("GPT 最终分析事实包", markdown)
        self.assertIn('"prediction"', markdown)
        self.assertIn('"actual_result"', markdown)
        self.assertIn('"evaluation"', markdown)
        self.assertIn('"modules"', markdown)
        self.assertIn('"source_warnings"', markdown)
        self.assertIn('"confidence": 0.72', markdown)
        self.assertIn('"final_score": "1-0"', markdown)
        self.assertIn('"module_id": "team_analysis"', markdown)
        self.assertIn("所有改进建议必须由 GPT 生成", markdown)
        self.assertIn("否决标准", markdown)
        self.assertNotIn("复核 Correct Score 候选排序", markdown)
        self.assertNotIn("按市场冲突、球队状态和联赛分组复盘", markdown)


if __name__ == "__main__":
    unittest.main()
