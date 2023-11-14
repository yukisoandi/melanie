from __future__ import annotations

import asyncio
import contextlib
import gc


class MelanieMemoryCache:
    def __init__(self, maxsize: int = 1000000) -> None:
        from melanie import create_task

        self.cache = {}
        self.maxsize = maxsize
        self.lock = asyncio.Lock()
        self.check_task = create_task(self.check_size())

    def __del__(self) -> None:
        self.check_task.cancel()

    async def check_size(self) -> None:
        while True:
            await asyncio.sleep(300)
            async with self.lock:
                if len(self.cache) > self.maxsize:
                    gc.disable()
                    try:
                        self.cache = dict(list(self.cache.items())[-self.maxsize / 2])
                    finally:
                        gc.enable()

    async def set(self, key, value) -> bool:
        async with self.lock:
            self.cache[key] = bytes(value)
            return True

    async def clear(self, ident) -> None:
        async with self.lock:
            if len(self.cache) > 1000:
                gc.disable()
                gc_disabled = True
            else:
                gc_disabled = False
            try:
                self.cache = {k: v for k, v in self.cache.items() if not k.startswith(ident)}
            finally:
                if gc_disabled:
                    gc.enable()

    async def get(self, key):
        async with self.lock:
            return self.cache.get(key)

    async def delete(self, key) -> None:
        async with self.lock:
            with contextlib.suppress(KeyError):
                del self.cache[key]
