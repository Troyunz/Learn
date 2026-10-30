"""Simple, consistent logging for every script and module in the project.

Using a shared factory keeps the log format identical across the pipeline and avoids the
duplicate-handler problem that occurs when a module is imported more than once.
"""

from __future__ import annotations

import logging
import os
import sys

#: Format shared by every logger in the project.
LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

#: Timestamp format used in log records.
DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

#: Fallback level used when nothing else is configured.
DEFAULT_LEVEL: str = "INFO"


def _resolve_level() -> str:
    """Read the configured log level at call time.

    Resolving lazily (rather than at import time) means the level applies no matter
    whether ``src.config`` is imported before or after this module.

    Returns:
        The configured level name, defaulting to ``INFO``.
    """
    return os.environ.get("LAKEHOUSE_LOG_LEVEL", DEFAULT_LEVEL).upper()


def get_logger(name: str, level: str | int | None = None) -> logging.Logger:
    """Return a configured logger that writes to stdout.

    The logger is configured only once per name; calling this function repeatedly with
    the same ``name`` returns the same logger without stacking extra handlers (which
    would otherwise cause every message to be printed multiple times).

    Args:
        name: Logger name, conventionally ``__name__`` or the script name.
        level: Optional level override, either a string such as ``"DEBUG"`` or a
            ``logging`` integer constant. Defaults to the level configured by
            ``config/<env>.yml`` or the ``LAKEHOUSE_LOG_LEVEL`` environment variable.

    Returns:
        A ready-to-use :class:`logging.Logger`.

    Example:
        >>> log = get_logger("01_bronze_ingestion")
        >>> log.info("Ingested %s rows", 8)
    """
    logger = logging.getLogger(name)
    logger.setLevel(level if level is not None else _resolve_level())

    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(handler)
        # Avoid double output if the root logger also has a handler attached.
        logger.propagate = False

    return logger


def log_section(logger: logging.Logger, title: str) -> None:
    """Emit a visually distinct section banner to make long pipeline logs scannable.

    Args:
        logger: The logger to write to.
        title: Short description of the stage that is starting.
    """
    logger.info("=" * 70)
    logger.info(title)
    logger.info("=" * 70)
