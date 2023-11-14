from __future__ import annotations

import asyncio
import datetime
import io
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

import discord
import orjson
import tuuid
from humanize.time import precisedelta
from unidecode import unidecode

from executionstracker.constants import BAD_CHANNEL_NAMES
from melanie import BaseModel, get_redis
from melanie.curl import worker_download
from runtimeopt.global_dask import get_dask

CONVERSION_PATH = Path("/cache/stickerconv")
for name in BAD_CHANNEL_NAMES:
    BAD_CHANNEL_NAMES.remove(name)
    name = " ".join(name.split()).lower()
    BAD_CHANNEL_NAMES.append(name)


def channel_name_is_ok(channel: Union[str, discord.TextChannel]) -> bool:
    name = unidecode(str(channel))
    name = " ".join(name.split()).lower()
    return all(i not in name for i in BAD_CHANNEL_NAMES)


class ExecutionEntry(BaseModel):
    message_id: int
    created_at: Optional[datetime.datetime]
    guild_id: Optional[int]
    guild_name: Optional[str]
    channel_id: Optional[int]
    channel_name: Optional[str]
    user_id: Optional[int]
    user_name: Optional[str]
    message: Optional[str]
    invoked_with: Optional[str]
    failed: Optional[bool]
    prefix: Optional[str]
    subcommand: Optional[str]
    args: Optional[Any]
    command: Optional[str]
    error: Optional[str]
    bot_user: str
    duration: float


def go_time() -> datetime.datetime:
    return datetime.datetime.now()


def exec_dur(end_time: datetime.datetime) -> str:
    diff = end_time - datetime.datetime.now()
    return precisedelta(diff, minimum_unit="milliseconds")


def create_name():
    return tuuid.tuuid()


@contextmanager
def borrow_temp_file(input_path_or_bytes=None, extension: str = "mp4") -> Path:
    if input_path_or_bytes:
        if isinstance(input_path_or_bytes, bytes):
            temp = CONVERSION_PATH / f"{tuuid.tuuid()}.{extension}"
            temp.write_bytes(input_path_or_bytes)
        else:
            temp = Path(str(input_path_or_bytes))
    else:
        temp = CONVERSION_PATH / f"{tuuid.tuuid()}.{extension}"
    try:
        yield temp
    finally:
        temp.unlink(missing_ok=True)


def download_sticker_gif(url) -> bytes:
    from melanie import log

    with borrow_temp_file(extension="png") as infile:
        return _extracted_from_download_sticker_gif_9(infile, url, subprocess, log)


def _extracted_from_download_sticker_gif_9(infile, url, subprocess, log):
    from melanie import log

    outfile = Path(str(infile).replace("png", "gif"))
    outfile: Path
    infile: Path
    infile.write_bytes(worker_download(url))
    cmd = f"apng2gif {infile}"
    o = subprocess.getoutput(cmd)
    log.info(o)
    data = outfile.read_bytes()
    outfile.unlink(missing_ok=True)
    return data


class CachedUserSQL(BaseModel):
    last_seen: datetime.datetime
    guild_name: str
    user_name: str
    user_id: int


class RelaySticker(BaseModel):
    id: int
    name: str

    @property
    def url(self) -> str:
        return f"https://media.discordapp.net/stickers/{self.id}.png?size=4096"

    @property
    def gif_key(self) -> str:
        return f"stickerdlgif:{self.id}"

    async def gif_url(self, bot):
        redis = get_redis()
        cache_channel: discord.TextChannel = bot.get_channel(1001689617310945320)
        if not cache_channel:
            msg = "No cache channel"
            raise RuntimeError(msg)
        gif_url = await redis.get(self.gif_key)
        if gif_url:
            return orjson.loads(gif_url)
        dask = get_dask()
        img_data = await dask.submit(download_sticker_gif, self.url, pure=False)
        file = discord.File(io.BytesIO(img_data), filename=f"{self.gif_key}.gif")
        sent: discord.Message = await cache_channel.send(file=file, delete_after=80)
        sticker_url = sent.attachments[0].url
        await redis.set(self.gif_key, orjson.dumps(sticker_url), ex=70)
        return sticker_url


class RelayMessage(BaseModel):
    guild_id: int
    guild_name: str
    stickers: Optional[list[RelaySticker]]
    channel_name: str
    channel_id: int
    id: int
    author_name: str
    author_id: str
    author_avatar_url: str
    jump_url: str
    content: str = None
    attachments: list = None
    embeds: list = None


class XrayRelay(BaseModel):
    target_channel: int
    create_guild: discord.Guild
    task: asyncio.Task


class ChannelRelay(BaseModel):
    actives: list[XrayRelay] = []
    channel: discord.TextChannel


class MutualGuildsRequest(BaseModel):
    user_id: int
    publish_channel: str


class MutualGuildData(BaseModel):
    guild_name: str = None
    guild_id: int
    premium_tier: int
    member_count: int
    icon_url: Optional[str] = None
    owner_id: int
    created_at: int
    banner_url: Optional[str] = None
    description: Optional[str] = None


class MutualGuildsResponse(BaseModel):
    guilds: list[MutualGuildData]


ACTIVE_QUERY = """


with ag1
as (
    select time_bucket('1m', created_at) tb,
        guild_id,
           last(id,created_at) id,
        count(*) cnt
    from discord_stats.guild_messages
    where created_at > (now() - interval %s minute)
    group by tb,
        guild_id
    )
select avg(cnt) avg,
    ag1.guild_id,
    guild_messages.guild_name,
    last(member_count, join_date) member_count

from ag1

join guild_messages on guild_messages.id = ag1.id
join member_join mj on ag1.guild_id = mj.guild_id

group by ag1.guild_id
order by avg desc


"""


class FakeRole:
    """We need to fake some attributes of roles for the class UnavailableMember."""

    position = 0
    colour = discord.Embed.Empty

    # @property
    # @abc.abstractmethod
    # def display_name(self):
    #     raise NotImplementedError

    # @property
    # @abc.abstractmethod
    # def mention(self):
    #     raise NotImplementedError

    # @classmethod
    # def __subclasshook__(cls, C):
    #     if cls is User:
    #         if Snowflake.__subclasshook__(C) is NotImplemented:

    #         for attr in ('display_name', 'mention', 'name', 'avatar', 'discriminator', 'bot'):
    #             for base in mro:
    #                 if attr in base.__dict__:


class UnavailableMember(discord.abc.User, discord.abc.Messageable):
    """A class that reproduces the behaviour of a discord.Member instance, except
    the member is not in the guild.

    This is used to prevent calling bot.fetch_info which has a very high
    cooldown.

    """

    def __init__(self, bot, state, result: CachedUserSQL) -> None:
        self.bot = bot
        self._state = state
        self.lookup = result.dict()
        self.id = result.user_id
        self.top_role = FakeRole()
        self.username = result.user_name
        self.name = result.user_name.split("#")[0]
        try:
            self.discriminator = result.user_name.split("#")[1]
        except Exception:
            self.discriminator = "0000"

    @property
    def display_name(self):
        return self.name

    @property
    def last_guild(self):
        return self.lookup["guild_name"]

    @property
    def last_seen(self):
        return self.lookup["created_at"].strftime("%m/%d, %H:%M:%S")

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def avatar_url(self) -> str:
        return "https://cdn.discordapp.com/embed/avatars/3.png"

    def __str__(self) -> str:
        return self.username

    @property
    def dm_channel(self):
        """Optional[:class:`DMChannel`]: Returns the channel associated with this user if it exists.
        If this returns ``None``, you can create a DM channel by calling the
        :meth:`create_dm` coroutine function.
        """
        return self._state._get_private_channel_by_user(self.id)

    async def create_dm(self):
        """Creates a :class:`DMChannel` with this user.

        This should be rarely called, as this is done transparently for
        most people.

        """
        found = self.dm_channel
        if found is not None:
            return found

        state = self._state
        data = await state.http.start_private_message(self.id)
        return state.add_dm_channel(data)

    async def _get_channel(self):
        return await self.create_dm()
