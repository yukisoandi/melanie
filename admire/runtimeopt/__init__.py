from __future__ import annotations

import asyncio
import concurrent.futures as cf
import itertools
import os
from asyncio import Future, Task
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Union

import uvloop  # noqa
from loguru import logger as log
from tornado.ioloop import IOLoop

uvloop.install()
from runtimeopt.offloaded import DEBUG, offloaded

_task_name_counter = itertools.count(1)


def return_task_results(task: Union[Task, Future]) -> None:
    if task.cancelled() or task.cancelling():
        return
    if task._exception is not None:
        try:
            task.result()
        except (SystemExit, KeyboardInterrupt, asyncio.CancelledError, cf.CancelledError):
            return
        except Exception:
            log.opt(exception=True).exception("Task factory returns an exception..")


def task_factory(loop: asyncio.AbstractEventLoop, coro, *, context=None, name=None):
    return asyncio.Task(coro, loop=loop, context=context, eager_start=True, name=name)


def loop_factory():
    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    exe = cf.ThreadPoolExecutor(32, thread_name_prefix="cfThread")
    loop.set_default_executor(exe)
    asyncio.set_event_loop(loop)
    return loop


def create_task(coro, *, name: Optional[str] = None, conext=None):
    """Schedule the execution of a coroutine object in a spawn task.

    Return a Task object.

    """
    if not name:
        name = f"task_{next(_task_name_counter)}"
    task = asyncio.create_task(coro, name=name, context=conext)
    if not hasattr(task, "tagged"):
        task.add_done_callback(return_task_results)
        task.tagged = True
    return task
