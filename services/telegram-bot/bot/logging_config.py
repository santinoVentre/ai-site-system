"""Structured logging for telegram-bot (JSON via loguru)."""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from typing import Any

from loguru import logger as _loguru_logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except (ValueError, AttributeError):
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            if frame.f_back is None:
                break
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _json_sink(message: Any) -> None:
    record = message.record
    payload: dict[str, Any] = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "service": record["extra"].get("service", ""),
        "logger": record["name"],
        "message": record["message"],
    }
    extras = {k: v for k, v in record["extra"].items() if k != "service"}
    if extras:
        payload["extra"] = extras
    if record["exception"] is not None:
        payload["exception"] = str(record["exception"])
    sys.stdout.write(_json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def configure_logging(service_name: str, *, level: str | None = None, json_logs: bool | None = None) -> None:
    level_value = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    if json_logs is None:
        json_logs = os.environ.get("LOG_JSON", "1").lower() in {"1", "true", "yes"}
    _loguru_logger.remove()
    _loguru_logger.configure(extra={"service": service_name})
    if json_logs:
        _loguru_logger.add(_json_sink, level=level_value)
    else:
        _loguru_logger.add(sys.stdout, level=level_value,
                           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(level_value)
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
        stdlib_logger = logging.getLogger(noisy)
        stdlib_logger.handlers = [_InterceptHandler()]
        stdlib_logger.propagate = False
    _loguru_logger.info("Logging configured", level=level_value, json=json_logs)
