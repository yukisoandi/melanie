from __future__ import annotations

import asyncio
import logging
import os
import typing
from asyncio import Future, Task
from collections.abc import Coroutine
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from datetime import timezone
from functools import partial, reduce
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union
from urllib.parse import urlparse

import httpx
import msgspec
import orjson
import regex as re
import tldextract
from aiomisc.utils import cancel_tasks  # noqa
from asyncer import asyncify as threaded
from boltons.iterutils import remap
from boltons.strutils import bytes2human as _bytes2human
from httpx._client import Response, URLTypes
from httpx._urls import URL
from humanize import intcomma
from humanize import intword as _intword
from loguru import logger as log
from partd import suppress
from regex.regex import Pattern
from tldextract.tldextract import ExtractResult
from tornado.escape import url_unescape
from tornado.ioloop import IOLoop
from xxhash import xxh32_hexdigest

from runtimeopt import _task_name_counter, return_task_results
from runtimeopt.global_dask import get_dask

from .cache.dict import LRI as LRICache
from .mime_table import mimes
from .models.base import BaseModel
from .scope import get_parent_var

P = typing.ParamSpec("P")
T = typing.TypeVar("T")
MENTION_RE: Pattern[str] = re.compile("<(@|#)[0-9]{18,19}>|<a{0,1}:[a-zA-Z0-9_.]{2,32}:[0-9]{18,19}>")
SMARTQUOTE_TABLE: dict[str, str] = {"–": "-", "—": "--", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."}


def normalize_smartquotes(value: str) -> str:
    for target, replacement in SMARTQUOTE_TABLE.items():
        value = value.replace(target, replacement)
    return value


def deepgetattr(obj, attr):
    """Recurses through an attribute chain to get the ultimate value."""
    return reduce(getattr, attr.split("."), obj)


@threaded
def tld_extractor(url: str) -> ExtractResult:
    return tldextract.extract(url)


def get_filename_from_url(url) -> str:
    return Path(urlparse(url_unescape(url)).path).name


def discord_cleaned(data: str) -> str:
    return MENTION_RE.sub(data, "")


def jsonloads(data) -> Union[list, dict, str]:
    return msgspec.json.decode(data)


def jsondumps(obj) -> bytes:
    return msgspec.json.encode(obj)


catcher = partial(log.catch, reraise=True, exclude=(asyncio.CancelledError))

SECONDS_IN_DAY = 86400
SECONDS_IN_HOUR = 3600
SECONDS_IN_WEEK = 604800
SECONDS_IN_MONTH = 604800
SECONDS_IN_YEAR = 31536000
if TYPE_CHECKING:
    import discord
    import discord.http
    from distributed import Client
    from melaniebot.core.bot import Melanie as Bot

    from melanie.redis import MelanieRedis
    from melanie.stats import MelanieStatsPool
S_1 = re.compile("([A-Z][a-z]+)")
S_2 = re.compile("([A-Z]+)")


def snake_cased(s) -> str:
    return "_".join(S_1.sub(r" \1", S_2.sub(r" \1", s.replace("-", " "))).split()).lower()


def snake_cased_dict(obj: dict, remove_nulls: bool = True, all_nulls: bool = False, discard_keys=[]) -> dict:
    discard_keys = set(discard_keys)

    def _visit(p, k, v):
        k = snake_cased(str(k))
        if k in discard_keys or (remove_nulls and ((not v and all_nulls) or v == "")):
            return False
        return (k, v)

    return remap(obj, visit=_visit)


def url_to_mime(url) -> tuple[Optional[str], str]:
    """Guess the mime from a URL.

    Args:
    ----
        url (str)

    Returns:
    -------
        tuple[str, str]: Returns the mime and the suffix
    """
    suffix = Path(urlparse(url_unescape(url)).path).suffix
    return (mimes.get(suffix), suffix)


class MelanieHttpx(httpx.AsyncClient):
    async def request(self, method: str, url: URLTypes, *a, **ka) -> Response:
        if json := ka.get("json"):
            if "headers" not in ka:
                ka["headers"] = {}
            ka["headers"]["content-type"] = "application/json"
            ka["data"] = orjson.dumps(ka.pop("json")).decode("UTF-8")

        return await super().request(method, url, *a, **ka)


def memberkey(ident: str, member: Union[discord.Member, tuple[int, int]]) -> str:
    """Generate a member key for caching or locks.

    Args:
    ----
        ident (str): Identity of the cog/function
        member (Union[discord.Member, Tuple[int, int]]): Data to identify the member. Either discord.Member or a tuple of GuildID, MemberID

    Returns:
    -------
        str: xxhash key

    """
    if isinstance(member, tuple):
        guild_id = member[0]
        member_id = member[1]
    else:
        guild_id = member.guild.id
        member_id = member.id

    return f"{ident}:{xxh32_hexdigest(str(guild_id))}:{xxh32_hexdigest(str(member_id))}"


async def build_cnx() -> tuple[MelanieRedis, MelanieStatsPool, Client]:
    from distributed import Client

    from melanie.redis import MelanieRedis

    return (
        await MelanieRedis.from_url(),
        None,
        await Client(
            os.getenv("DASK_HOST"),
            direct_to_workers=True,
            asynchronous=True,
            name="debugjp",
        ),
    )


def discord_ts(obj, attr: str = "created_at") -> int:
    dt = getattr(obj, attr)
    return dt.replace(tzinfo=timezone.utc).timestamp()


def get_htx() -> httpx.AsyncClient:
    from melanie.bot import GLOBAL_HTTPX

    if not GLOBAL_HTTPX:
        msg = "No HTX sesion"
        raise ValueError(msg)

    return GLOBAL_HTTPX[0]


def get_bot() -> Bot:
    from melanie.bot import GLOBAL_BOT

    return GLOBAL_BOT[0]


def orjson_dumps(v, *a) -> str:
    # orjson.dumps returns bytes, to match standard json.dumps we need to decode
    return orjson.dumps(v).decode("UTF-8")


def _save_file_sync(url: typing.Union[URL, str], dest, timeout: int = 60) -> None:
    with httpx.Client(
        http2=True,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout=timeout),
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=3),
    ) as htx:
        file = Path(dest)
        with htx.stream("GET", url) as r, file.open("wb") as target:
            for data in r.iter_bytes():
                target.write(data)


async def download_file_url(url, dest, timeout: int = 60) -> bool:
    dask = get_dask()
    await dask.submit(_save_file_sync, url, dest, timeout, pure=False)
    return True


def bytes2human(len_or_bytes: typing.Union[bytes, int], ndigits: int = 0):
    if isinstance(len_or_bytes, bytes):
        return _bytes2human(len(len_or_bytes), ndigits)
    elif not isinstance(len_or_bytes, int):
        msg = "Input must be bytes or number of bytes"
        raise ValueError(msg)
    return _bytes2human(len_or_bytes, ndigits)


def default_lock_cache(max_size: int = 5000) -> dict[Any, asyncio.Lock]:
    return LRICache(max_size=max_size, on_miss=lambda x: asyncio.Lock())


def make_key(item) -> str:
    return xxh32_hexdigest(str(item))


def catch_exceptions(task: Union[Task, Future]) -> None:
    task.add_done_callback(return_task_results)


def _check(p, k, v):
    return False if v is None else (k, v)


def remove_nulls(obj: dict):
    return remap(obj, visit=_check)


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


def strip_image(data: bytes) -> bytes:
    from wand.image import Image

    with Image(blob=data) as img:
        img.strip()
        img.interlace_scheme
        result = img.make_blob()
    return result


def spawn_task(coro_or_task: Coroutine, list_of_tasks: list) -> asyncio.Task:
    """Spawn a task with self removal from a list.

    Returns the task object created

    """
    task = create_task(coro_or_task)
    list_of_tasks.append(task)
    task.add_done_callback(list_of_tasks.remove)
    return task


class CallingStackInfo(BaseModel):
    func_name: str
    line_num: int
    filename: str
    stack_info: Optional[str]


async def logctx(msg) -> None:
    if ctx := get_parent_var("ctx") or get_parent_var("_ctx"):
        await ctx.send(str(msg))
    log.info(msg)


async def delete_conf_slow(confirmation, delete_delay) -> None:
    import discord

    if delete_delay:
        await asyncio.sleep(delete_delay)
        with suppress(discord.HTTPException):
            await confirmation.quit()


async def yesno(
    question: str,
    body: str = "",
    ok_title: typing.Optional[str] = None,
    ok_body: str = "",
    timeout: int = 70,
    delete_delay: float | None = 6.5,
) -> tuple[bool, discord.Message]:
    from melanie.vendor.disputils import BotConfirmation

    loop = IOLoop.current()
    ctx = get_parent_var("ctx") or get_parent_var("_ctx")
    confirmation = BotConfirmation(ctx, 0x010101)

    await confirmation.confirm(question, hide_author=True, timeout=timeout, description=body)
    if confirmation.confirmed:
        if not ok_title:
            ok_title = "Confirmed!"
        await confirmation.update(ok_title, color=0x55FF55, description=ok_body, hide_author=True)
        loop.add_callback(delete_conf_slow, confirmation, delete_delay)
        return True, confirmation.message
    else:
        await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")
        loop.add_callback(delete_conf_slow, confirmation, delete_delay)
        return False, confirmation.message


def findCaller(stack_info: bool = False, levels: int = 1) -> CallingStackInfo:
    try:
        a = logging.Logger.findCaller(None, True, levels)
        return CallingStackInfo(filename=a[0], line_num=a[1], func_name=a[2], stack_info=a[4] if stack_info else None)
    except Exception:
        return log.warning("Unable to extract frame")


def intword(val: int) -> str:
    if not val:
        val = 0
    if val < 10000:
        return intcomma(val)
    a = _intword(val)
    a = a.replace("thousand", "k")
    a = a.replace(" k", "k")
    return a


async def send_embed(msg: str, status: typing.Union[int, str] = 1, tip: typing.Optional[str] = None, **ka) -> None:
    from melanie import make_e

    ctx = get_parent_var("ctx")
    embed = make_e(msg, status=status, tip=tip)
    await ctx.send(embed=embed, **ka)


def hex_to_int(hexstr: str) -> int:  # sourcery skip: remove-unnecessary-cast
    """Convert an HTML hex code to a decimal color for Discord Embeds.

    Args:
    ----
        hexstr (str): ie #FF5700 - Can have # with or without

    Returns:
    -------
        int: _description_

    """
    hexstr = str(hexstr)

    return int(hexstr.replace("#", ""), 16)


def unpackb(data: bytes) -> object:
    return msgspec.msgpack.decode(data)


def packb(item: object) -> bytes:
    return msgspec.msgpack.encode(item)


def noop(*a, **ka) -> bool:
    return True


class nullcontext(AbstractContextManager, AbstractAsyncContextManager):
    """Context manager that does no additional processing.

    Used as a stand-in for a normal context manager, when a particular
    block of code is only sometimes used with a normal context manager:

    cm = optional_cm if condition else nullcontext()
    with cm:
        # Perform operation, using optional_cm if condition is True

    """

    def __init__(self, enter_result=None) -> None:
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *excinfo) -> None:
        pass

    async def __aenter__(self):
        return self.enter_result

    async def __aexit__(self, *excinfo) -> None:
        pass
