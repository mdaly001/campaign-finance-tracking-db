"""JSON structured logging via structlog."""

import logging
import sys

import structlog


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure structured JSON logging via structlog.

    Uses ``structlog``'s ``JSONRenderer`` by default so every log
    record is a single JSON line — ideal for structured log consumers
    (ELK, Grafana Loki, Datadog, etc.).

    Args:
        level: Logging level string (e.g. ``"DEBUG"``, ``"INFO"``, ``"WARNING"``).
        log_file: Optional file path. If None, logs go to stdout.
    """
    stdlib_level = getattr(logging, level.upper(), logging.INFO)

    # Clear existing handlers to avoid duplicate logs
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(stdlib_level)

    # Build the formatter using ProcessorFormatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
        ],
    )

    if log_file:
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Configure structlog's own logger
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# Convenience logger
log: structlog.stdlib.BoundLogger | None = None


def get_logger() -> structlog.stdlib.BoundLogger:
    """Return a pre-configured structlog logger.

    Returns a logger that will work after ``setup_logging()`` has been
    called.  If ``setup_logging`` has not yet been called, falls back
    to a basic stdlib logger.
    """
    global log
    if log is None:
        import logging

        log = structlog.wrap_logger(logging.getLogger("cfdb"))
    return log


# Module-level logger — set after setup_logging() is called.
logger = get_logger()
