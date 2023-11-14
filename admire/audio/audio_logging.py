from __future__ import annotations

import logging
from typing import Any

from loguru import logger

IS_DEBUG = False


def is_debug() -> bool:
    return IS_DEBUG


def debug_exc_log(lg: logging.Logger, exc: Exception, msg: str = None, *args: tuple[Any]) -> None:
    """Logs an exception if logging is set to DEBUG level."""
    logger.opt(depth=1, exception=exc).exception(f"Exception {exc} - {msg}")
