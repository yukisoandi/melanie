from __future__ import annotations

import asyncio
import contextlib
from asyncio import Future, Task
from collections.abc import Coroutine
from typing import AnyStr, Union

from loguru import logger as log


class TaskNameSpace:
    def __init__(self, name) -> None:
        self.lock = asyncio.Lock()
        self.name = name
        self.tasks = {}

    @classmethod
    def create(cls, name):
        return TaskNameSpace(str(name))


class MelanieTaskGuard:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Future] = {}
        self.tasks = asyncio.LifoQueue()
        self.control_lock = asyncio.Lock()
        self.ncancelled = 0
        self.ns_locks: dict[str, asyncio.Lock] = {}
        self.nfailed = 0
        self.loop = asyncio.get_running_loop()

    async def recycler(self):
        while True:
            task: asyncio.Future = await self.tasks.get()
            if not task.cancelled():
                task.exception()

    def add_errcnt(self):
        self.nfailed += 1

    async def get_named_lock(self, name):
        async with self.control_lock:
            if name not in self.ns_locks:
                self.ns_locks[name] = asyncio.Lock()
            return self.ns_locks[name]

    def remove_result(self, task: asyncio.Future):
        try:
            if not task.cancelled():
                with log.catch(exclude=asyncio.CancelledError, onerror=self.add_errcnt):
                    task.result()
            else:
                self.ncancelled += 1
        finally:
            self.loop.call_soon(self.loop_del, task.key)

    def loop_del(self, key):
        with contextlib.suppress(KeyError, AttributeError):
            del self.tasks[key]

    async def spawn_task(self, task: Union[Coroutine, Future, Task], caller_ident: AnyStr):
        caller_ident = str(caller_ident)
        async with await self.get_named_lock(caller_ident):
            spawned = asyncio.ensure_future(task, loop=self.loop)
            spawned.key = f"{caller_ident}_{id(task)}"
            self.tasks[spawned.key] = spawned
            self.tasks[spawned.key].add_done_callback(self.remove_result)
            return spawned

    async def shutdown_namespace(self, namespace: AnyStr):
        async with await self.get_named_lock(namespace):
            tasks = [t for key, t in self.tasks.items() if key.startswith(str(namespace))]

            if tasks:
                [t.cancel() for t in tasks]
                async with asyncio.timeout(10):
                    await asyncio.gather(*tasks, return_exceptions=True)
            return len(tasks)
