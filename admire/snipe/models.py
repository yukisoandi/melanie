from __future__ import annotations

import asyncio
import io
from typing import Optional, Union

import arrow
import discord
import orjson
from melaniebot.core.bot import Melanie
from xxhash import xxh3_64_hexdigest, xxh32_hexdigest

from melanie import BaseModel, get_filename_from_url, get_redis
from melanie.curl import AsyncHTTPClient
from runtimeopt import offloaded

SNIPE_TTL = 64800

CachedStatsMessage = None

fromdatetime = arrow.Arrow.fromdatetime


class Attachment(BaseModel):
    filename: str
    payload: bytes

    def make_filename(self) -> str:
        return f"melanieSniped_{xxh3_64_hexdigest(self.payload)}{self.filename[-5:]}"

    def to_discord_file(self) -> discord.File:
        return discord.File(io.BytesIO(self.payload), filename=self.make_filename())

    async def set_item(self):
        redis = get_redis()
        return await redis.set(self.key, self.to_bytes(), ex=SNIPE_TTL)

    @property
    def key(self) -> str:
        return f"snipefile:{xxh3_64_hexdigest(self.payload)}"

    @staticmethod
    async def from_cached_message(message: CachedStatsMessage | discord.Message | discord.Attachment) -> list[Attachment]:
        return await asyncio.gather(*[asyncio.wait_for(save_attachment(i), timeout=12) for i in message.attachments])


async def save_attachment(i: str | discord.Attachment, cache_item: bool = True) -> Attachment:
    curl = AsyncHTTPClient()
    if isinstance(i, discord.Attachment):
        url = i.url
        url2 = i.proxy_url
    else:
        url = i
        url2 = None

    if not url:
        return
    r = await curl.fetch(url, raise_error=False)
    if url2 and r.error:
        url = url2
        r = await curl.fetch(str(url2), raise_error=False)
    if r.error:
        return None

    item = Attachment(filename=get_filename_from_url(url), payload=bytes(r.body))
    if cache_item:
        await item.set_item()
    return item


class StickerMessageSnipe(BaseModel):
    id: int
    name: Optional[str]
    format: Optional[str]

    @property
    def url(self) -> str:
        return f"https://media.discordapp.net/stickers/{self.id}.png?size=1024"

    @property
    def cache_key(self) -> str:
        return f"stickergif:{self.url}"

    async def convert_to_gif(self, bot: Melanie) -> bytes:
        redis = get_redis()
        img_bytes = await redis.get(self.cache_key)
        if not img_bytes:
            async with asyncio.timeout(9):
                img_bytes = await _convert_to_gif(self.url)

        return img_bytes


class MessageSnipe(BaseModel):
    message_id: int
    channel_id: int
    guild_id: int
    content: str
    user_id: int
    user_name: str
    created_at: float
    deleted_at: float
    avatar_icon_url: Optional[str]
    attachment_keys: list[str] = []
    stickers: list[StickerMessageSnipe] = []
    loaded_attachments: list[Attachment] = []

    @property
    def was_bot_filtered(self) -> bool:
        return self.deleted_at - self.created_at < 0.520

    @staticmethod
    def make_channel_key(channel: Union[discord.TextChannel, int]) -> str:
        channel_id = str(channel.id) if isinstance(channel, discord.TextChannel) else str(channel)
        return f"channelsnipe:{xxh32_hexdigest(channel_id)}"

    @classmethod
    async def from_cache(cls, payload: bytes):
        cls = cls(**orjson.loads(payload))
        if cls.attachment_keys:
            redis = get_redis()
            for k in cls.attachment_keys:
                data = await redis.get(k)
                if not data:
                    continue
                cls.loaded_attachments.append(Attachment.from_bytes(data))
        return cls


class ReactionSnipe(BaseModel):
    message_id: int
    guild_id: int
    channel_id: int
    timestamp: float
    user_id: int
    user_name: str
    emote_name: Optional[str]
    emote_url: Optional[str]

    @staticmethod
    def make_channel_key(channel: Union[discord.TextChannel, int]) -> str:
        channel_id = str(channel.id) if isinstance(channel, discord.TextChannel) else str(channel)
        return f"reactsnipe:{xxh32_hexdigest(channel_id)}"

    @property
    def message_link(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"


@offloaded
def _convert_to_gif(url) -> bytes:
    import io

    from PIL import Image

    from melanie.curl import worker_download

    _data = worker_download(url)
    data = io.BytesIO(_data)
    img = Image.open(data)
    output = io.BytesIO()
    img.save(output, save_all=True, format="gif")
    return output.getvalue()
