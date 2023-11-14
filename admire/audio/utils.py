from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import MutableMapping
from enum import Enum, unique

import discord
from melaniebot.core import commands

from melanie import get_redis, log


def _(x):
    return x


class CacheLevel:
    __slots__ = ("value",)

    def __init__(self, level: int = 0) -> None:
        if not isinstance(level, int):
            msg = f"Expected int parameter, received {level.__class__.__name__} instead."
            raise TypeError(msg)
        elif level < 0:
            level = 0
        elif level > 0b11111:
            level = 0b11111

        self.value = level

    def __eq__(self, other):
        return isinstance(other, CacheLevel) and self.value == other.value

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.value)

    def __add__(self, other):
        return CacheLevel(self.value + other.value)

    def __radd__(self, other):
        return CacheLevel(other.value + self.value)

    def __sub__(self, other):
        return CacheLevel(self.value - other.value)

    def __rsub__(self, other):
        return CacheLevel(other.value - self.value)

    def __str__(self) -> str:
        return f"{self.value:b}"

    def __format__(self, format_spec) -> str:
        return "{r:{f}}".format(r=self.value, f=format_spec)

    def __repr__(self) -> str:
        return f"<CacheLevel value={self.value}>"

    def is_subset(self, other):
        """Returns ``True`` if self has the same or fewer caching levels as other."""
        return (self.value & other.value) == self.value

    def is_superset(self, other):
        """Returns ``True`` if self has the same or more caching levels as other."""
        return (self.value | other.value) == self.value

    def is_strict_subset(self, other):
        """Returns ``True`` if the caching level on other are a strict subset of
        those on self.
        """
        return self.is_subset(other) and self != other

    def is_strict_superset(self, other):
        """Returns ``True`` if the caching level on other are a strict superset of
        those on self.
        """
        return self.is_superset(other) and self != other

    __le__ = is_subset
    __ge__ = is_superset
    __lt__ = is_strict_subset
    __gt__ = is_strict_superset

    @classmethod
    def all(cls):
        """A factory method that creates a :class:`CacheLevel` with max caching
        level.
        """
        return cls(0b11111)

    @classmethod
    def none(cls):
        """A factory method that creates a :class:`CacheLevel` with no caching."""
        return cls(0)

    @classmethod
    def set_spotify(cls):
        """A factory method that creates a :class:`CacheLevel` with Spotify
        caching level.
        """
        return cls(0b00011)

    @classmethod
    def set_youtube(cls):
        """A factory method that creates a :class:`CacheLevel` with YouTube
        caching level.
        """
        return cls(0b00100)

    @classmethod
    def set_lavalink(cls):
        """A factory method that creates a :class:`CacheLevel` with lavalink
        caching level.
        """
        return cls(0b11000)

    def _bit(self, index):
        return bool((self.value >> index) & 1)

    def _set(self, index, value):
        if value is True:
            self.value |= 1 << index
        elif value is False:
            self.value &= ~(1 << index)
        else:
            msg = "Value to set for CacheLevel must be a bool."
            raise TypeError(msg)

    @property
    def lavalink(self):
        """:class:`bool`: Returns ``True`` if a user can deafen other users."""
        return self._bit(4)

    @lavalink.setter
    def lavalink(self, value) -> None:
        self._set(4, value)

    @property
    def youtube(self):
        """:class:`bool`: Returns ``True`` if a user can move users between other voice
        channels.
        """
        return self._bit(2)

    @youtube.setter
    def youtube(self, value) -> None:
        self._set(2, value)

    @property
    def spotify(self):
        """:class:`bool`: Returns ``True`` if a user can use voice activation in voice channels."""
        return self._bit(1)

    @spotify.setter
    def spotify(self, value) -> None:
        self._set(1, value)


class Notifier:
    def __init__(self, ctx: commands.Context, message: discord.Message, updates: MutableMapping, **kwargs) -> None:
        self.context = ctx
        self.message = message
        self.updates = updates
        self.color = None
        self.last_msg_time = 0
        self.cooldown = 5

    async def notify_user(self, current: int = None, total: int = None, key: str = None, seconds_key: str = None, seconds: str = None) -> None:
        """This updates an existing message.

        Based on the message found in :variable:`Notifier.updates` as
        per the `key` param

        """
        if self.last_msg_time + self.cooldown > time.time() and current != total:
            return
        if self.color is None:
            self.color = await self.context.embed_colour()
        embed2 = discord.Embed(colour=self.color, title=self.updates.get(key, "").format(num=current, total=total, seconds=seconds))
        if seconds and seconds_key:
            embed2.set_footer(text=self.updates.get(seconds_key, "").format(seconds=seconds))
        with contextlib.suppress(discord.errors.NotFound):
            _edit_key = f"audioedit:{self.message.id}"
            redis = get_redis()
            if not await redis.ratelimited(_edit_key, 2, 1):
                await self.message.edit(embed=embed2)
                self.last_msg_time = int(time.time())

    async def update_text(self, text: str) -> None:
        embed2 = discord.Embed(colour=self.color, title=text)
        with contextlib.suppress(discord.errors.NotFound):
            await self.message.edit(embed=embed2)

    async def update_embed(self, embed: discord.Embed) -> None:
        with contextlib.suppress(discord.errors.NotFound):
            await self.message.edit(embed=embed)
            self.last_msg_time = int(time.time())


@unique
class PlaylistScope(Enum):
    GLOBAL = "GLOBALPLAYLIST"
    GUILD = "GUILDPLAYLIST"
    USER = "USERPLAYLIST"

    def __str__(self) -> str:
        return f"{self.value}"

    @staticmethod
    def list():
        return [c.value for c in PlaylistScope]


def task_callback(task: asyncio.Task) -> None:
    with contextlib.suppress(asyncio.CancelledError, asyncio.InvalidStateError):
        if exc := task.exception():
            log.exception("{} raised an Exception", task.get_name(), exc_info=exc)


def has_internal_server():
    async def pred(ctx: commands.Context) -> bool:
        external = await ctx.cog.config.use_external_lavalink()
        return not external

    return commands.check(pred)
