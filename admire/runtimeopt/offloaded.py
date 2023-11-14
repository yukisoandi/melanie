from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from functools import partial
from typing import Awaitable, Callable, ParamSpec, TypeVar

import distributed.client
from tornado import gen

from .global_dask import get_dask

P = ParamSpec("P")
T = TypeVar("T")


def strtobool(val) -> bool:
    if not val:
        return False
    val = str(val)
    val = val.lower()
    if val in {"y", "yes", "t", "true", "on", "1"}:
        return True
    elif val in {"n", "no", "f", "false", "off", "0"}:
        return False
    else:
        msg = f"invalid truth value {val!r}"
        raise ValueError(msg)


DEBUG = strtobool(os.getenv("DEBUG", "OFF"))


@gen.coroutine
def cascade_future(future: distributed.Future, cf_future: asyncio.Future):
    result = yield future._result(raiseit=False)
    status = future.status
    if status == "finished":
        with suppress(asyncio.InvalidStateError):
            cf_future.set_result(result)
    elif status == "cancelled":
        cf_future.cancel()
        # Necessary for wait() and as_completed() to wake up
        cf_future.set_running_or_notify_cancel()
    else:
        try:
            typ, exc, tb = result
            raise exc.with_traceback(tb)
        except BaseException as exc:
            cf_future.set_exception(exc)


def cf_callback(cf_future):
    if cf_future.cancelled() and cf_future.dask_future.status != "cancelled":
        asyncio.ensure_future(cf_callback.dask_future.cancel())


def offloaded(f: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    async def offloaded_task(*a, **ka):
        loop = asyncio.get_running_loop()
        cf_future = loop.create_future()
        dask = get_dask()
        meth = partial(f, *a, **ka)
        cf_future.dask_future = dask.submit(meth, pure=True)
        cf_future.dask_future.add_done_callback(cf_callback)
        cascade_future(cf_future.dask_future, cf_future)
        return await cf_future

    return offloaded_task
