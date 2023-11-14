from __future__ import annotations

import asyncio
import contextvars
import functools
import pickle
import types
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "shards": 16,
    "directory": "/cache/central_diskcache",
    "statistics": 0,
    "tag_index": 0,
    "eviction_policy": "least-recently-stored",
    "size_limit": 100073741824,
    "cull_limit": 10,
    "sqlite_auto_vacuum": 1,
    "sqlite_cache_size": 2**13,
    "sqlite_journal_mode": "wal",
    "sqlite_mmap_size": 2**26,
    "sqlite_synchronous": 1,
    "disk_min_file_size": 2**15,
    "disk_pickle_protocol": pickle.HIGHEST_PROTOCOL,
}  # False  # False  # 1gb  # FULL  # 8,192 pages  # 64mb  # NORMAL  # 32kb


def whoami() -> tuple[str, types.ModuleType]:
    import inspect
    import sys

    f = sys._getframe(1)
    callers_code = f.f_code
    callers_mod = inspect.getmodule(callers_code)

    return {"name": callers_code.co_name, "mod": callers_mod, "frame": inspect.getframeinfo(f), "f": f, "co": callers_code}


async def to_thread(func, /, *args, **kwargs):
    """Asynchronously run function *func* in a separate thread.

    Any *args and **kwargs supplied for this function are directly
    passed to *func*. Also, the current :class:`contextvars.Context` is
    propagated, allowing context variables from the main thread to be
    accessed in the separate thread. Return a coroutine that can be
    awaited to get the eventual result of *func*.

    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)


def size_in_bytes_gb(size_in_gb: int) -> int:
    return 1073741824 * size_in_gb


def collect_results(f: asyncio.Future) -> None:
    from loguru import logger as log

    try:
        f.result()

    except Exception:
        log.exception("Error collecting Task")
