"""Structured JSON logging.

Application logs and the logs emitted by uvicorn and SQLAlchemy are routed
through one structlog pipeline so every line is a single JSON object with
consistent keys. A ``request_id`` bound via context variables is attached
automatically, which is what correlates an HTTP request to its log lines and to
its audit rows (CLAUDE.md §6).

Every event passes through :mod:`app.core.redaction` before rendering, on both
the structlog path and the stdlib path used by third-party libraries. Redaction
sits at the sink because credentials reach logs by accident — a header dump, an
exception repr, a driver logging its DSN — and no discipline at the call sites
catches all of those.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.core.config import Settings
from app.core.redaction import redaction_processor

_STDLIB_LOGGERS_TO_TAME = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "sqlalchemy.engine",
    "httpx",
    "httpcore",
)
_QUIET_THIRD_PARTY_LOGGERS = ("httpx", "httpcore")


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib logging module.

    Safe to call more than once; later calls replace the previous configuration.
    """
    level = getattr(logging, settings.log_level, logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            # Last before hand-off to the formatter, so it sees the fully
            # assembled event including anything merged in from contextvars.
            redaction_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # Applied again here because log records from third-party libraries
            # enter through foreign_pre_chain and never pass through the
            # structlog processor chain above. A library logging a connection
            # string is exactly the leak this catches.
            redaction_processor,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Let these propagate to the root handler rather than writing their own
    # unstructured lines to stderr.
    for name in _STDLIB_LOGGERS_TO_TAME:
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers.clear()
        stdlib_logger.propagate = True

    # httpx's INFO record contains the complete request URL. Amazon report
    # downloads use presigned URLs and inventory pagination uses opaque tokens,
    # neither of which belongs in durable logs. Application-level events still
    # record operation names, status, page number, and retry outcomes.
    for name in _QUIET_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger.

    The return type is intentionally ``Any``: structlog's ``BoundLogger`` gains
    its methods dynamically, so a narrower annotation would be a lie that mypy
    could not check usefully.
    """
    return structlog.stdlib.get_logger(name)
