from __future__ import annotations

import logging
import re
from datetime import datetime
from logging import FileHandler
from pathlib import Path
from threading import local

from app.logging_setup import LOG_FORMAT


_THREAD_STATE = local()
_QA_PROJECT_LOGGER = logging.getLogger("app.qa")


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sanitize_question(question: str, max_length: int = 48) -> str:
    collapsed = re.sub(r"\s+", "-", (question or "").strip())
    sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "", collapsed).strip("-_")
    if not sanitized:
        sanitized = "question"
    return sanitized[:max_length]


def _question_log_dir() -> Path:
    log_dir = _project_root() / "logs" / "qa"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def build_question_log_path(question: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return (_question_log_dir() / f"{timestamp}_{_sanitize_question(question)}.log").resolve()


class QuestionLogSession:
    def __init__(self, question: str) -> None:
        self.question = question
        self.log_path = build_question_log_path(question)
        self.logger = logging.getLogger(f"app.qa.question.{self.log_path.stem}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._handler: FileHandler | None = None

    def __enter__(self) -> Path:
        handler = FileHandler(self.log_path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        self.logger.handlers.clear()
        self.logger.addHandler(handler)
        self._handler = handler
        _THREAD_STATE.active_question_logger = self.logger
        return self.log_path

    def __exit__(self, exc_type, exc, tb) -> None:
        current_logger = getattr(_THREAD_STATE, "active_question_logger", None)
        if current_logger is self.logger:
            _THREAD_STATE.active_question_logger = None
        if self._handler is not None:
            self.logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None


def emit_qa_log(step: str, message: str) -> None:
    formatted = f"（{step}） {message}"
    _QA_PROJECT_LOGGER.info(formatted)
    question_logger = getattr(_THREAD_STATE, "active_question_logger", None)
    if question_logger is not None:
        question_logger.info(formatted)
