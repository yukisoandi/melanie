from __future__ import annotations

import datetime
import os
from collections import defaultdict, deque
from functools import partial
from typing import TYPE_CHECKING, Optional, Union

import yarl
from async_lru import alru_cache
from asyncer import asyncify
from boltons.cacheutils import LRI
from humanize import precisedelta
from loguru import logger as log
from tornado import gen
from tornado.ioloop import IOLoop
from tornado.locks import Semaphore
from xxhash import xxh3_64_hexdigest

from melanie.curl import CurlError, get_curl, url_concat
from melanie.models.colors import ColorPalette, curl_download_url
from melanie.redis import get_redis
from melanie.tenor import TenorResult
from melanie.vendor.disputils import BotConfirmation

footer_gif = "https://media.discordapp.net/attachments/782123801319440384/839483147740905492/839437202164547654.gif"
TENOR_KEY = "25FRLM2Z8FYU"

if TYPE_CHECKING:
    import discord

try:
    _img_cache  # type:ignore
except NameError:
    _img_cache = LRI(500)


sems = defaultdict(partial(Semaphore, 64))


@asyncify
def extract_json_tag(data, key_ident: str, soap=True) -> bytes:
    import orjson
    from bs4 import BeautifulSoup
    from loguru import logger as log

    with log.catch():

        def seq_checker(value):
            if len(value) == 1:
                value = [value]
            to_check = deque(value)

            def mapping_checker(value: dict):
                for v in value.values():
                    if type(v) == list:
                        to_check.extend(v)
                        continue
                    if type(v) == dict:
                        to_check.append(v)
                        continue
                    if type(v) == str:
                        try:
                            data = orjson.loads(v)
                            to_check.append(data)
                        except Exception:
                            continue

            rounds = 0
            while to_check:
                item = to_check.pop()
                rounds += 1
                if type(item) == list:
                    to_check.extend(item)
                    continue
                if type(item) == dict:
                    if key_ident in item:
                        return item
                    item = mapping_checker(item)
                    if type(item) == list:
                        to_check.extend(result)
                        continue
                if type(item) == str:
                    try:
                        item = orjson.loads(item)
                        to_check.append(item)
                    except Exception:
                        continue

            return None

        if soap:
            soup = BeautifulSoup(data, "lxml")
            for x in soup.find_all("script", type="application/json"):
                result = seq_checker(orjson.loads(x.decode_contents()))
                if result:
                    return orjson.dumps(result)

        else:
            result = seq_checker(orjson.loads(data))
            if result:
                return orjson.dumps(result)

        return False


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


def get_api_baseurl(*a) -> str:
    url = os.getenv("API_BASE_URL", "https://dev.melaniebot.net")
    for arg in a:
        url = f"{url}/{arg}"
    return url


class UnknownType(Exception):
    pass


class Timeit:
    def __init__(self, name: str) -> None:
        self.start_time = datetime.datetime.now()
        self.name = name

    def done(self) -> str:
        diff = self.start_time - datetime.datetime.now()
        return precisedelta(diff, format="%0.4f")


async def ask_for_conf(ctx, header: str, body: str, ok_title_msg: str, ok_body_msg: str = "", footer=None, timeout: int = 75) -> bool:
    loop = IOLoop.current()
    confirmation = BotConfirmation(ctx, 0x010101)
    await confirmation.confirm(header, hide_author=True, timeout=timeout, description=body)
    if confirmation.confirmed:
        loop.add_callback(confirmation.update, ok_title_msg, color=0x55FF55, description=ok_body_msg, hide_author=True)
        return True
    else:
        loop.add_callback(confirmation.update, "Request cancelled", hide_author=True, color=0xFF5555, description="")
        return False


def rgb_to_int(rgb: tuple[int]) -> int:
    decimal = rgb[0]
    decimal = (decimal << 8) + rgb[1]
    return (decimal << 8) + rgb[2]


@alru_cache
async def fetch_gif_if_tenor(content: Optional[str]) -> str | None:
    url = yarl.URL(content)
    if not url.host or not url.scheme:
        return None
    if content.lower().endswith((".gif", ".mp4", ".jpg", ".jpeg", ".png", ".webp")):
        return content
    key = str(url).split("-")[-1]
    if not key:
        return None
    redis = get_redis()
    lookup = await redis.hget("tenorgif", key)
    if lookup:
        return lookup.decode("UTF-8")
    curl = get_curl()
    url = url_concat("https://g.tenor.com/v1/gifs", {"ids": key, "key": TENOR_KEY})
    try:
        r = await curl.fetch(url)
    except CurlError as e:
        return log.debug("Tenor returned with an error result of {}", e)
    data = TenorResult.parse_raw(r.body)
    if not data.results:
        return
    t = data.results[0]
    gif_direct_url = t.media[0].gif.url
    await redis.hset("tenorgif", key, gif_direct_url)
    return gif_direct_url


@gen.coroutine
def get_image_colors2(url_or_bytes: str | bytes) -> ColorPalette:
    import distributed
    import orjson

    from melanie.models.colors import build_palettes_4

    redis = get_redis()
    key = xxh3_64_hexdigest(url_or_bytes)
    if key not in _img_cache:
        cached = yield redis.hget("imgquant", key)
        if cached:
            _img_cache[key] = ColorPalette(colors=orjson.loads(cached))
            return _img_cache[key]

    if key not in _img_cache:
        dask = distributed.default_client()
        data = dask.submit(curl_download_url, url_or_bytes) if isinstance(url_or_bytes, str) else url_or_bytes
        task = dask.submit(build_palettes_4, data)

        cached = yield task

        if cached:
            lookup = ColorPalette(colors=orjson.loads(cached))
            yield redis.hset("imgquant", key, cached)
            _img_cache[key] = lookup
        else:
            _img_cache[key] = None
    return _img_cache[key]


def make_e(description: str, status: Union[str, int] = 1, color: int = 0x010101, tip: Optional[str] = None) -> discord.Embed:
    """Generate an embed."""
    import discord

    if status in ["love"]:
        emote = "💌"
        color = 0xF2F1D9

    if status in [1, "ok"]:
        emote = "✅"
        color = 0x83AF5F

    if status in ["quiet", "music"]:
        emote = "🎵"
        color = 0x83AF5F
    elif status == "question":
        emote = "🤔"
        color = 0xE8C34A
    elif status == 2:
        emote = "⚠️"
        color = 0xE8C34A
    elif status == 3:
        emote = "⛔️"
        color = 0xFF5555
    elif status in ["lock", "locked"]:
        emote = "🔐"
        color = color

    elif status in ["unlock", "unlocked"]:
        emote = "🔓"
        color = color

    elif status == "info":
        emote = "ℹ️"
        color = 0x3F7CB9

    elif status in ["welc", "welcome"]:
        emote = "👋"
        color = 0x3F7CB9

    des = f"{emote}   {description}"
    em = discord.Embed(description=des, color=color)
    if tip or emote == "👋":
        em.set_footer(text=tip or "melanie", icon_url=footer_gif)

    return em
