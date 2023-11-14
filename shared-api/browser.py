from __future__ import annotations

import asyncio
import itertools
import os
import random
import time
from collections import Counter, defaultdict
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

import msgpack
import orjson
import tuuid
from aiomisc import cancel_tasks
from anyio import CapacityLimiter
from loguru import logger as log
from melanie import capturetime, create_task, get_redis, spawn_task
from playwright._impl._connection import Connection, ProtocolCallback
from playwright._impl._driver import compute_driver_executable
from playwright._impl._object_factory import create_remote_object
from playwright._impl._transport import PipeTransport
from playwright.async_api._generated import Playwright as _AsyncPlaywright
from pyarrow import fs
from runtimeopt import DEBUG
from yarl import URL

from api_services import services
from core import BrowserDataExtractor

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from types import TracebackType

    from playwright.async_api import Browser, BrowserContext, Page

MAX_CONCURRENCY_PER_CTX: int = 2


class AsyncPlaywright(AbstractAsyncContextManager, _AsyncPlaywright):
    _connection: Connection = None
    _callbacks: dict[int, ProtocolCallback] = {}
    _runtask: asyncio.Task = None

    @classmethod
    async def build(cls) -> AsyncPlaywright:
        loop = asyncio.get_running_loop()
        _connection = Connection(None, create_remote_object, PipeTransport(loop, compute_driver_executable()), loop)
        _connection.mark_as_remote()
        _runtask = loop.create_task(_connection.run())
        playwright_future = _connection.playwright_future
        done, _ = await asyncio.wait({_connection._transport.on_error_future, playwright_future}, return_when=asyncio.FIRST_COMPLETED)
        f = done.pop()
        f2 = await f
        cls = cls(f2)
        cls._connection = _connection
        cls._runtask = _runtask
        return cls

    async def __aenter__(self):
        return self

    def cleanup(self, error_message: str | None = None) -> None:
        if not error_message:
            error_message = "Connection closed"
        self._connection._closed_error_message = error_message
        if self._connection._init_task and not self._connection._init_task.done():
            self._connection._init_task.cancel()
        for ws_connection in self._connection._child_ws_connections:
            ws_connection._transport.dispose()
        ends = [callback.future for callback in self._connection._callbacks.values()]
        cancel_tasks(ends)

        self._connection._callbacks.clear()

    async def _shutdown(self):
        await asyncio.sleep(0.1)
        self.cleanup()
        self._connection._transport.request_stop()
        await self._connection._transport.wait_until_stopped()
        self._connection._callbacks.clear()
        await self.stop()

    async def __aexit__(self, __exc_type: type[BaseException] | None, __exc_value: BaseException | None, __traceback: TracebackType | None) -> bool | None:
        await self._shutdown()
        return None


class BrowserContextHolder:
    def __init__(self, stack: AsyncExitStack) -> None:
        self.stack = stack
        self.fs = fs.LocalFileSystem()
        self.browsers: dict[str, Browser] = {}
        self.browser: Browser = None
        self.key = "api_sessions_store2"
        self.browser_contexts: dict[str, BrowserContext] = {}
        self.built_event = asyncio.Event()
        self.invalid_holder = defaultdict(list)
        self.save_loop = create_task(self._save_loop())
        self.proxy_browser: Browser = None
        self.ctx_iterator: Iterator = None
        self.limiter = CapacityLimiter(12)
        self.proxy_pages: asyncio.LifoQueue[Page] = asyncio.LifoQueue()
        self.pages: dict[str, asyncio.LifoQueue[Page]] = defaultdict(asyncio.LifoQueue)
        self.save_lock = asyncio.Lock()
        self.redis = get_redis()
        self.flag_wait = 3600
        self.control_lock = asyncio.Lock()
        self.page_lock = asyncio.Lock()
        self.total_pages = 0
        self.total_proxy_pages = 0
        self.default_args: dict[str, str]
        self.rankings = Counter()
        self.proxy_context: BrowserContext = None
        self.usercycle: itertools.cycle[str] = None

    async def detect_closed(self, page: Page):
        log.error("Detected a page close!")
        if username := getattr(page, "username", None):
            log.warning("Submitting a shutdown from context {}", username)
            services.ioloop.add_callback(services.shutdown_later)

    def flag_context_page(self, username: str):
        key = f"api_flagged_context:{username}"
        spawn_task(self.redis.set(key, str(time.time()), ex=self.flag_wait), services.active_tasks)
        log.error("FLAGGED CONTEXT - {} has been disabled for {} seconds and will be discarded from the pool", username, self.flag_wait)

    async def is_flagged_page(self, name: str):
        if await self.redis.exists(f"api_flagged_context:{name}"):
            log.warning("Context {} is flag disabled ", name)
            return True
        else:
            return False

    async def get_page(self, user: str) -> Page:
        if services.closed:
            msg = "closed!"
            raise RuntimeError(msg)
        async with asyncio.timeout(90):
            while True:
                if user:
                    return await self.pages[user].get()
                else:
                    username = next(self.usercycle)
                if self.pages[username].qsize() and (not await self.is_flagged_page(username) and not await self.redis.sismember("disabled_ctx", username)):
                    return await self.pages[username].get()
                else:
                    await asyncio.sleep(0.1)

    @property
    def number_free_pages(self) -> int:
        return sum(bucket.qsize() for bucket in self.pages.values())

    @asynccontextmanager
    async def borrow_page(self, proxy: bool = False, user: str | None = None) -> Generator[Page, None, None]:
        await self.limiter.acquire()
        if proxy:
            page = await self.proxy_pages.get()

            log.info("Obtained proxy page ID {}.  {}/{} pages borrowed", id(page), self.total_proxy_pages - self.proxy_pages.qsize(), self.total_proxy_pages)
        else:
            page = await self.get_page(user)

            log.warning("Obtained page ID {}. {}/{} pages borrowed", page.username, self.total_pages - self.number_free_pages, self.total_pages)
        try:
            yield page
        finally:
            self.limiter.release()
            if proxy:
                self.proxy_pages.put_nowait(page)
            else:
                self.pages[page.username].put_nowait(page)
                log.success("Page ID {} restored. {}/{} pages borrowed", page.username, self.total_pages - self.number_free_pages, self.total_pages)

    async def save(self) -> None:
        redis = get_redis()
        await redis.set("proxy_state", msgpack.packb(await self.proxy_context.storage_state()))
        for name, ctx in self.browser_contexts.items():
            with capturetime(f"session save {name}"):
                await asyncio.sleep(0.12)
                while True:
                    if self.limiter.borrowed_tokens != 0:
                        await asyncio.sleep(0.1)
                    else:
                        async with self.page_lock:
                            await redis.hset(self.key, name, msgpack.packb(await ctx.storage_state()))
                            break

    async def _save_loop(self) -> None:
        await self.built_event.wait()
        await asyncio.sleep(3)

        if DEBUG:
            return log.warning("Save loop is not going to run because we are in debug")
        with log.catch(exclude=asyncio.CancelledError):
            await self.save()

        while True:
            sleep = random.uniform(200, 300)
            await asyncio.sleep(sleep)
            with log.catch(exclude=asyncio.CancelledError):
                await self.save()

    async def build(self) -> None:
        redis = get_redis()

        self.playwright = await AsyncPlaywright.build()
        await self.stack.enter_async_context(self.playwright)
        self.default_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
        }

        ident = f"proxy_{tuuid.tuuid()}"

        browser_url = URL(os.environ["BROWSER_URL"])
        browser_url = browser_url.with_query(
            {
                "blockAds": "true",
                "headless": "new",
                "ignoreHTTPSErrors": "true",
                "stealth": "true",
                "trackingId": ident,
                "--proxy-server": "socks5://warp:1080",
            },
        )
        self.proxy_browser = await self.playwright.chromium.connect_over_cdp(str(browser_url))

        self.proxy_context = await self.proxy_browser.new_context(
            **orjson.loads(orjson.dumps(self.default_args)),
            storage_state=msgpack.unpackb(await redis.get("proxy_state")),
        )

        while self.proxy_pages.qsize() < 5:
            page = await self.proxy_context.new_page()

            log.success("Created page {} Name: {}", id(page), "PROXY")
            page.username = "proxy"

            await self.proxy_pages.put(page)
            await asyncio.sleep(0.1)

        async for username, value in redis.hscan_iter(self.key):
            username = str(username.decode())
            ident = f"{username}_{tuuid.tuuid()}"

            browser_url = URL(os.environ["BROWSER_URL"])
            browser_url.with_query(
                {
                    "stealth": "true",
                    "trackingId": ident,
                    "blockAds": "true",
                },
            )

            self.browsers[username] = await self.playwright.chromium.connect_over_cdp(str(browser_url))
            self.browser_contexts[username] = await self.browsers[username].new_context(
                storage_state=msgpack.unpackb(value),
                **orjson.loads(orjson.dumps(self.default_args)),
            )

            for _ in range(MAX_CONCURRENCY_PER_CTX):
                page = await self.browser_contexts[username].new_page()
                page.username = username
                page.on("close", self.detect_closed)
                await self.pages[username].put(page)
                log.success("Created page {} Name: {}", id(page), username)

            if DEBUG:
                break

        ctx_names = [str(x) for x in self.browser_contexts]
        random.shuffle(ctx_names)
        self.usercycle = itertools.cycle(ctx_names)
        self.total_pages = self.number_free_pages
        self.total_proxy_pages = self.proxy_pages.qsize()
        log.success("Page holder event is SET")
        services.insta = BrowserDataExtractor(services=services, browser=self.browser, redis=services.redis, page_holder=self)
        self.built_event.set()
