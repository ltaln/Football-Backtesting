import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from database.models import BacktestTask, EvaluationRecord


class Database:
    SQLITE_TIMEOUT_SECONDS = 15
    SQLITE_BUSY_TIMEOUT_MS = 15000

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.SQLITE_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.SQLITE_BUSY_TIMEOUT_MS}")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS backtest_tasks (
                    task_id TEXT PRIMARY KEY, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    status TEXT NOT NULL, snapshot_id TEXT, created_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY, version TEXT NOT NULL, source TEXT NOT NULL,
                    pollution_status TEXT NOT NULL, created_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, match_id TEXT NOT NULL,
                    prediction_json TEXT NOT NULL, actual_json TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL, error_type TEXT
                );
                CREATE TABLE IF NOT EXISTS replay_range_runs (
                    range_run_id TEXT PRIMARY KEY, parent_request_id TEXT NOT NULL,
                    command TEXT NOT NULL, task_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_time REAL NOT NULL
                );
            """)

    def activate_replay_run(self, range_run_id: str, parent_request_id: str,
                            command: str, task_ids: list[str]) -> list[str]:
        """Atomically supersede older active replay groups and return their task ids."""
        with self.session() as db:
            active = db.execute(
                "SELECT range_run_id, task_ids_json FROM replay_range_runs WHERE status='ACTIVE'"
            ).fetchall()
            previous = [task_id for row in active if row["range_run_id"] != range_run_id
                        for task_id in json.loads(row["task_ids_json"])]
            db.execute(
                "UPDATE replay_range_runs SET status='SUPERSEDED' WHERE status='ACTIVE' AND range_run_id<>?",
                (range_run_id,),
            )
            db.execute(
                "INSERT OR REPLACE INTO replay_range_runs VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
                (range_run_id, parent_request_id, command,
                 json.dumps(task_ids, separators=(",", ":")), time.time()),
            )
        return previous

    def get_active_replay_run(self, command: str) -> dict | None:
        """Return the exact active group for a normalized replay command."""
        with self.session() as db:
            row = db.execute(
                "SELECT range_run_id, parent_request_id, command, task_ids_json "
                "FROM replay_range_runs WHERE status='ACTIVE' AND command=? "
                "ORDER BY created_time DESC LIMIT 1",
                (command,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["task_ids"] = json.loads(result.pop("task_ids_json"))
        return result

    def list_active_replay_runs(self) -> list[dict]:
        with self.session() as db:
            rows = db.execute(
                "SELECT range_run_id, parent_request_id, command, task_ids_json "
                "FROM replay_range_runs WHERE status='ACTIVE' ORDER BY created_time DESC"
            ).fetchall()
        runs = []
        for row in rows:
            result = dict(row)
            result["task_ids"] = json.loads(result.pop("task_ids_json"))
            runs.append(result)
        return runs

    def update_replay_run_command(self, range_run_id: str, command: str) -> None:
        with self.session() as db:
            db.execute(
                "UPDATE replay_range_runs SET command=? WHERE range_run_id=? AND status='ACTIVE'",
                (command, range_run_id),
            )

    def retire_replay_run(self, range_run_id: str, status: str = "STALE") -> None:
        with self.session() as db:
            db.execute(
                "UPDATE replay_range_runs SET status=? WHERE range_run_id=? AND status='ACTIVE'",
                (status, range_run_id),
            )

    def complete_replay_run(self, task_ids: list[str]) -> None:
        encoded = json.dumps(task_ids, separators=(",", ":"))
        with self.session() as db:
            db.execute(
                "UPDATE replay_range_runs SET status='COMPLETED' WHERE status='ACTIVE' AND task_ids_json=?",
                (encoded,),
            )

    def create_task(self, task: BacktestTask) -> None:
        with self.session() as db:
            db.execute("INSERT INTO backtest_tasks VALUES (?, ?, ?, ?, ?, ?)", tuple(task.as_dict().values()))

    def update_task(self, task_id: str, status: str, snapshot_id: str | None = None) -> None:
        with self.session() as db:
            if snapshot_id is None:
                db.execute("UPDATE backtest_tasks SET status=? WHERE task_id=?", (status, task_id))
            else:
                db.execute("UPDATE backtest_tasks SET status=?, snapshot_id=? WHERE task_id=?", (status, snapshot_id, task_id))

    def get_task(self, task_id: str) -> dict | None:
        with self.session() as db:
            row = db.execute("SELECT * FROM backtest_tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def save_snapshot(self, snapshot: dict) -> None:
        with self.session() as db:
            db.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                (snapshot["snapshot_id"], snapshot["version"], snapshot["source"], snapshot["pollution_status"], snapshot["created_time"]),
            )

    def save_evaluation(self, task_id: str, record: EvaluationRecord) -> None:
        with self.session() as db:
            db.execute(
                "INSERT INTO evaluations (task_id, match_id, prediction_json, actual_json, evaluation_json, error_type) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, record.match_id, record.prediction_json, record.actual_json, record.evaluation_json, record.error_type),
            )
