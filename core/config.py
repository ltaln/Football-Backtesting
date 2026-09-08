import logging
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    max_backtest_days: int = 3
    database_path: Path = Path(os.getenv("HH520_DATABASE", BASE_DIR / "data" / "hh520_backtest.db"))
    snapshot_dir: Path = Path(os.getenv("HH520_SNAPSHOT_DIR", BASE_DIR / "data" / "snapshots"))
    archive_dir: Path = Path(os.getenv("HH520_ARCHIVE_DIR", BASE_DIR / "data" / "archive"))
    report_dir: Path = Path(os.getenv("HH520_REPORT_DIR", BASE_DIR / "data" / "reports"))
    log_path: Path = Path(os.getenv("HH520_LOG_PATH", BASE_DIR / "logs" / "hh520.log"))
    prediction_archive_dir: Path | None = Path(os.environ["HH520_PREDICTION_ARCHIVE_DIR"]) if os.getenv("HH520_PREDICTION_ARCHIVE_DIR") else None
    replay_prediction_dir: Path | None = Path(os.environ["HH520_REPLAY_PREDICTION_DIR"]) if os.getenv("HH520_REPLAY_PREDICTION_DIR") else None


def configure_logging(settings: Settings) -> None:
    if logging.getLogger().handlers:
        return
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(settings.log_path, encoding="utf-8"), logging.StreamHandler()],
    )


DEFAULT_SETTINGS = Settings()
