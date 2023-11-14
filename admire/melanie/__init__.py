from __future__ import annotations

import asyncio
import concurrent.futures as cf
import contextlib
import os
import sys
import typing
from asyncio import timeout
from asyncio import timeout as Timeout
from asyncio.events import AbstractEventLoop
from asyncio.taskgroups import TaskGroup as Tg

import aiohttp
import aiohttp.connector
import aiohttp.resolver
from aioitertools.asyncio import as_completed
from anyio import Path as AsyncPath
from anyio.abc import Listener, TaskGroup
from async_lru import alru_cache
from asyncer import asyncify as threded
from boltons.iterutils import is_iterable
from boltons.urlutils import *
from humanize import intcomma
from humanize import intword as _intword
from loguru import logger as log
from xxhash import xxh32_hexdigest, xxh64_hexdigest
from xxhash import xxh32_hexdigest as x3hash

from runtimeopt.global_dask import GLOBAL_DASK, get_dask

from .cache.dict import LRI as LRICache
from .cache.disk import *
from .core import *
from .core import create_task as spawn
from .curl import *
from .deepreload import deepreload
from .helpers import *
from .humanbytes import *
from .mime_table import guess_extension2, guess_mime2, mimes
from .models import *
from .models.base import *
from .redis import *
from .scope import *
from .stats import *
from .strutils import *
from .timing import capturetime, fmtseconds
from .uploads import *
from .utils import *

aiohttp.resolver.aiodns_default = True
aiohttp.resolver.DefaultResolver = aiohttp.resolver.AsyncResolver
aiohttp.connector.DefaultResolver = aiohttp.resolver.AsyncResolver


async def checkpoint(n: int = 0) -> None:
    return await asyncio.sleep(n)


# pylint: disable=invalid-name
T = typing.TypeVar("T")
P = typing.ParamSpec("P")


def get_import_str(obj, use_type: bool = False):
    import dill

    if use_type:
        obj = type(obj)
    imp = dill.source.getimport(obj)
    return imp.strip()


def _init_loop() -> AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.get_event_loop

    loop = asyncio.get_event_loop()
    import sys

    if sys.platform == "linux":
        watcher = asyncio.PidfdChildWatcher()

    else:
        watcher = asyncio.FastChildWatcher()
    watcher.attach_loop(loop)
    asyncio.set_child_watcher(watcher)
    loop._exec_pool = cf.ThreadPoolExecutor(34)
    loop.set_default_executor(loop._exec_pool)
    return loop


@contextlib.contextmanager
def set_env(**environ):
    """Temporarily set the process environment variables.

    >>> with set_env(PLUGINS_DIR=u'test/plugins'):
    ...   "PLUGINS_DIR" in os.environ
    True

    >>> "PLUGINS_DIR" in os.environ
    False

    :type environ: dict[str, unicode]
    :param environ: Environment variables to set

    """
    old_environ = dict(os.environ)
    os.environ |= environ
    try:
        yield
    finally:
        os.environ.clear()
        os.environ |= old_environ


async def tick(n: int = 0) -> None:
    return await asyncio.sleep(n)


@asynccontextmanager
async def borrow_temp_file(base="/tmp", extension=None) -> typing.Generator[AsyncPath, None, None]:
    if not extension:
        extension = ""
    file = AsyncPath(f"{base}/{tuuid.tuuid()}{extension}")
    try:
        yield file
    finally:
        await file.unlink(missing_ok=True)


@contextmanager
def borrow_temp_file_sync(base="/tmp", extension=None) -> typing.Generator[Path, None, None]:
    from pathlib import Path

    if not extension:
        extension = ""
    file = Path(f"{base}/{tuuid.tuuid()}{extension}")
    try:
        yield file
    finally:
        file.unlink(missing_ok=True)
