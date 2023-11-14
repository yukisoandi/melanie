from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from typing import Awaitable, Callable, ParamSpec, Type, TypeVar, Union

import humanize
from boltons.cacheutils import LRI, cached

try:
    from asyncio import Timeout
    from asyncio import timeout as Timeout  # noqa

except ImportError:
    pass


P = ParamSpec("P")

T = TypeVar("T")

logger2: logging.Logger = logging.getLogger(__name__)
_cache = LRI(1200)


def deadline(timeout_duration: float = None, exception_to_raise: Type[Exception] | None = asyncio.TimeoutError) -> Callable[P, Awaitable[T]]:
    def decorator(func: Callable[P, T]):
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            try:
                async with asyncio.timeout(timeout_duration):
                    return await func(*args, **kwargs)
            except TimeoutError:
                if exception_to_raise is not None:
                    raise exception_to_raise

        return async_wrapper

    return decorator


@cached(_cache)
def fmtseconds(seconds: Union[int, float], unit: str = "microseconds") -> str:
    """String representation of the amount of time passed.

    Args:
    ----
        seconds (Union[int, float]): seconds from ts
        minimum_unit: str

    """
    return humanize.precisedelta(timedelta(seconds=seconds), minimum_unit=unit)


@contextmanager
def capturetime(description: str = None) -> None:
    from loguru import logger as log

    start = time.time()
    if not description:
        description = logger2.findCaller()[2]
    try:
        yield
    finally:
        end = time.time()
        dur = fmtseconds(end - start)
        description = description.replace("<", "").replace(">", "")

        log.opt(depth=2, colors=True).info(f"<white>{description}</white> timed: <magenta>{dur}</magenta>")
