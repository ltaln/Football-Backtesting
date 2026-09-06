import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from analysis.error_analyzer import ErrorAnalyzer
from collector.collector_adapter import CollectorAdapter, LocalArchiveCollector
from collector.football_ai_adapter import FootballAIArchiveCollector
from core.command_parser import parse_command
from core.config import DEFAULT_SETTINGS, Settings, configure_logging
from database.db import Database
from database.models import BacktestTask, EvaluationRecord
from evaluation.evaluation_engine import EvaluationEngine
from report.report_engine import ReportEngine
from snapshot.snapshot_manager import SnapshotManager

WORKFLOW = ["CREATED", "CHECKING_DATA", "COLLECTING", "SANITIZING", "SNAPSHOT_READY", "RUNNING", "EVALUATING", "REPORT_READY"]


class TaskManager:
    def __init__(self, settings: Settings = DEFAULT_SETTINGS, collector: CollectorAdapter | None = None):
        self.settings = settings
        configure_logging(settings)
        self.log = logging.getLogger("hh520.task")
        self.db = Database(settings.database_path)
        replay_dir = settings.replay_prediction_dir or settings.prediction_archive_dir
        self.collector = collector or (FootballAIArchiveCollector(replay_dir) if replay_dir else LocalArchiveCollector(settings.archive_dir))
        self.snapshots = SnapshotManager(settings.snapshot_dir)
        self.evaluator = EvaluationEngine()
        self.analyzer = ErrorAnalyzer()
        self.reports = ReportEngine(settings.report_dir)

    def run(self, command_text: str, prediction_commit_ids: list[str] | None = None) -> dict:
        command = parse_command(command_text)
        task = BacktestTask(f"BT-{uuid4().hex[:12]}", command.start_date.isoformat(), command.end_date.isoformat(), "CREATED", None, datetime.now(timezone.utc).isoformat())
        self.db.create_task(task)
        try:
            self._status(task.task_id, "CHECKING_DATA")
            self._status(task.task_id, "COLLECTING")
            if prediction_commit_ids is None:
                candidate = self.collector.collect_history(command.start_date, command.end_date)
            else:
                candidate = self.collector.collect_history(command.start_date, command.end_date, prediction_commit_ids)
            if not candidate.get("matches"):
                raise ValueError("NO_MATCHES_IN_DATE_RANGE")
            self._status(task.task_id, "SANITIZING")
            snapshot = self.snapshots.freeze(candidate, command.start_date, command.end_date)
            snapshot_data = snapshot.as_dict()
            self.db.save_snapshot(snapshot_data)
            self._status(task.task_id, "SNAPSHOT_READY", snapshot.snapshot_id)
            self._status(task.task_id, "RUNNING")
            self._status(task.task_id, "EVALUATING")
            evaluations = []
            for match in snapshot.matches:
                evaluation = self.evaluator.evaluate(match)
                error_type = self.analyzer.classify(match, evaluation)
                result = {"match": match.get("match", match.get("match_id")), "prediction": match.get("prediction", {}), "actual_result": match.get("actual", {}), "evaluation": evaluation, "error_type": error_type}
                evaluations.append(result)
                self.db.save_evaluation(task.task_id, EvaluationRecord(match.get("match_id", "UNKNOWN"), json.dumps(match.get("prediction", {}), ensure_ascii=False), json.dumps(match.get("actual", {}), ensure_ascii=False), json.dumps(evaluation, ensure_ascii=False), error_type))
            self._status(task.task_id, "REPORT_READY", snapshot.snapshot_id)
            stored_task = self.db.get_task(task.task_id)
            report = self.reports.build(stored_task, snapshot_data, evaluations)
            self.log.info("task=%s status=REPORT_READY matches=%d", task.task_id, len(evaluations))
            return report
        except Exception:
            self._status(task.task_id, "FAILED")
            self.log.exception("task=%s status=FAILED", task.task_id)
            raise

    def status(self, task_id: str) -> dict | None:
        return self.db.get_task(task_id)

    def report(self, task_id: str) -> dict | None:
        return self.reports.get(task_id)

    def _status(self, task_id: str, status: str, snapshot_id: str | None = None) -> None:
        if status not in WORKFLOW and status != "FAILED":
            raise ValueError("INVALID_TASK_STATUS")
        self.db.update_task(task_id, status, snapshot_id)
        self.log.info("task=%s status=%s", task_id, status)
