
"""
Logging configuration utilities for vnpy_fubon.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional, Sequence


class GatewayContextFilter(logging.Filter):
    """
    Inject gateway context and monotonically increasing sequence numbers into log records.
    """

    def __init__(self, gateway_name: Optional[str] = None) -> None:
        super().__init__()
        self.gateway_name = gateway_name
        self._sequence = 0

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - required by logging.Filter
        self._sequence += 1
        if self.gateway_name and not hasattr(record, "gateway"):
            record.gateway = self.gateway_name
        if not hasattr(record, "seq"):
            record.seq = self._sequence
        return True


class StructuredJsonFormatter(logging.Formatter):
    """
    Render log records as JSON with optional domain-specific fields.
    """

    DEFAULT_EXTRA_FIELDS: Sequence[str] = (
        "gateway_state",
        "channel",
        "symbol",
        "seq",
        "latency_ms",
    )

    def __init__(self, extra_fields: Optional[Iterable[str]] = None) -> None:
        super().__init__()
        self.extra_fields = tuple(extra_fields or self.DEFAULT_EXTRA_FIELDS)

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - overrides base
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "gateway") and record.gateway is not None:
            payload["gateway"] = record.gateway

        for field in self.extra_fields:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    payload[field] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def _apply_formatter(logger: logging.Logger, formatter: logging.Formatter) -> None:
    for handler in logger.handlers:
        handler.setFormatter(formatter)


def configure_logging(
    log_level: int = logging.INFO,
    log_directory: Optional[Path] = None,
    logger_name: str = "vnpy_fubon",
    *,
    structured: bool = True,
    structured_fields: Optional[Iterable[str]] = None,
    gateway_name: Optional[str] = None,
) -> logging.Logger:
    """
    Create and configure a logger tailored for the connector.
    """

    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    logger.propagate = False

    if structured:
        formatter: logging.Formatter = StructuredJsonFormatter(structured_fields)
    else:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    if structured and not any(isinstance(flt, GatewayContextFilter) for flt in logger.filters):
        logger.addFilter(GatewayContextFilter(gateway_name or logger_name))

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        if log_directory:
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / f"{logger_name}.log"
            file_handler = RotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    else:
        _apply_formatter(logger, formatter)

    return logger
