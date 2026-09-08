from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOG_FILE_NAME = "backend.log"
LOG_FORMAT = "%(asctime)s | %(message)s"


def setup_daily_file_logging(project_root: Path) -> Path:
    """Configure project-wide daily rotating file logging.

    Logs are written to: <project_root>/logs/backend.log
    Rotated files are split by day with date suffix.
    """

    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / LOG_FILE_NAME
    resolved_log_file = log_file.resolve()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler):
            if Path(handler.baseFilename).resolve() == resolved_log_file:
                return resolved_log_file

    file_handler = TimedRotatingFileHandler(
        filename=str(resolved_log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root_logger.addHandler(file_handler)
    if root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)

    return resolved_log_file

