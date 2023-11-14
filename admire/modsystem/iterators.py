from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncIterator, Awaitable
from inspect import isawaitable
from typing import Any, Callable, NamedTuple, Optional, SupportsInt, TypeVar, Union

from discord.errors import NoMoreItems
from discord.http import Route
from discord.state import ConnectionState
from discord.user import User


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"


class BanEntry(NamedTuple):
    reason: Optional[str]
    user: Optional[User]


SupportsIntCast = Union[SupportsInt, str, bytes, bytearray]

T = TypeVar("T")

T = TypeVar("T")
OT = TypeVar("OT")
_Func = Callable[[T], Union[OT, Awaitable[OT]]]

DISCORD_EPOCH = 1420070400000


class EqualityComparable:
    __slots__ = ()

    id: int

    def __eq__(self, other: object) -> bool:
        return isinstance(other, self.__class__) and other.id == self.id

    def __ne__(self, other: object) -> bool:
        return other.id != self.id if isinstance(other, self.__class__) else True


class Hashable(EqualityComparable):
    __slots__ = ()

    def __hash__(self) -> int:
        return self.id >> 22


def snowflake_time(id: int) -> datetime.datetime:
    timestamp = ((id >> 22) + DISCORD_EPOCH) / 1000
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)


def time_snowflake(dt: datetime.datetime, high: bool = False) -> int:
    discord_millis = int(dt.timestamp() * 1000 - DISCORD_EPOCH)
    return (discord_millis << 22) + (2**22 - 1 if high else 0)


class Object(Hashable):
    def __init__(self, id: SupportsIntCast) -> None:
        try:
            id = int(id)
        except ValueError:
            msg = f"id parameter must be convertible to int not {id.__class__!r}"
            raise TypeError(msg) from None
        else:
            self.id = id

    def __repr__(self) -> str:
        return f"<Object id={self.id!r}>"

    @property
    def created_at(self) -> datetime.datetime:
        """:class:`datetime.datetime`: Returns the snowflake's creation time in UTC."""
        return snowflake_time(self.id)

    @property
    def worker_id(self) -> int:
        """:class:`int`: Returns the worker id that made the snowflake."""
        return (self.id & 0x3E0000) >> 17

    @property
    def process_id(self) -> int:
        """:class:`int`: Returns the process id that made the snowflake."""
        return (self.id & 0x1F000) >> 12

    @property
    def increment_id(self) -> int:
        """:class:`int`: Returns the increment id that made the snowflake."""
        return self.id & 0xFFF


OLDEST_OBJECT = Object(id=0)


async def maybe_coroutine(f, *args, **kwargs):
    value = f(*args, **kwargs)
    return await value if isawaitable(value) else value


class _AsyncIterator(AsyncIterator[T]):
    __slots__ = ()

    async def next(self) -> T:
        raise NotImplementedError

    def get(self, **attrs: Any) -> Awaitable[Optional[T]]:
        def predicate(elem: T) -> bool:
            for attr, val in attrs.items():
                nested = attr.split("__")
                obj = elem
                for attribute in nested:
                    obj = getattr(obj, attribute)

                if obj != val:
                    return False
            return True

        return self.find(predicate)

    async def find(self, predicate: _Func[T, bool]) -> Optional[T]:
        while True:
            try:
                elem = await self.next()
            except NoMoreItems:
                return None

            ret = await maybe_coroutine(predicate, elem)
            if ret:
                return elem

    def chunk(self, max_size: int) -> _ChunkedAsyncIterator[T]:
        if max_size <= 0:
            msg = "async iterator chunk sizes must be greater than 0."
            raise ValueError(msg)
        return _ChunkedAsyncIterator(self, max_size)

    def map(self, func: _Func[T, OT]) -> _MappedAsyncIterator[OT]:
        return _MappedAsyncIterator(self, func)

    def filter(self, predicate: _Func[T, bool]) -> _FilteredAsyncIterator[T]:
        return _FilteredAsyncIterator(self, predicate)

    async def flatten(self) -> list[T]:
        return [element async for element in self]

    async def __anext__(self) -> T:
        try:
            return await self.next()
        except NoMoreItems as e:
            raise StopAsyncIteration from e


def _identity(x):
    return x


class _MappedAsyncIterator(_AsyncIterator[T]):
    def __init__(self, iterator, func) -> None:
        self.iterator = iterator
        self.func = func

    async def next(self) -> T:
        # this raises NoMoreItems and will propagate appropriately
        item = await self.iterator.next()
        return await maybe_coroutine(self.func, item)


class _FilteredAsyncIterator(_AsyncIterator[T]):
    def __init__(self, iterator, predicate) -> None:
        self.iterator = iterator

        if predicate is None:
            predicate = _identity

        self.predicate = predicate

    async def next(self) -> T:
        getter = self.iterator.next
        pred = self.predicate
        while True:
            # propagate NoMoreItems similar to _MappedAsyncIterator
            item = await getter()
            ret = await maybe_coroutine(pred, item)
            if ret:
                return item


class _FilteredAsyncIterator(_AsyncIterator[T]):
    def __init__(self, iterator, predicate) -> None:
        self.iterator = iterator

        if predicate is None:
            predicate = _identity

        self.predicate = predicate

    async def next(self) -> T:
        getter = self.iterator.next
        pred = self.predicate
        while True:
            # propagate NoMoreItems similar to _MappedAsyncIterator
            item = await getter()
            ret = await maybe_coroutine(pred, item)
            if ret:
                return item


class _ChunkedAsyncIterator(_AsyncIterator[list[T]]):
    def __init__(self, iterator, max_size) -> None:
        self.iterator = iterator
        self.max_size = max_size

    async def next(self) -> list[T]:
        ret: list[T] = []
        n = 0
        while n < self.max_size:
            try:
                item = await self.iterator.next()
            except NoMoreItems:
                if ret:
                    return ret
                raise
            else:
                ret.append(item)
                n += 1
        return ret

    # if member.guild_permissions.administrator:

    # if member == guild.owner:

    # if me.top_role <= member.top_role:


class BanIterator(_AsyncIterator["BanEntry"]):
    def __init__(self, ctx, guild, limit=None, before=None, after=None) -> None:
        if isinstance(after, datetime.datetime):
            after = Object(id=time_snowflake(after, high=True))

        if isinstance(before, datetime.datetime):
            before = Object(id=time_snowflake(before, high=True))

        self.guild = guild
        self.ctx = ctx
        self.limit = limit
        self.after = after
        self.before = before

        self.state: ConnectionState = self.guild._state
        self.bans = asyncio.Queue()

    def get_bans(self, guild_id: int, limit: Optional[int] = None, before: Optional[int] = None, after: Optional[int] = None):
        params: dict[str, Union[int, int]] = {}

        if limit is not None:
            params["limit"] = limit
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after

        return self.ctx.bot.http.request(V9Route("GET", "/guilds/{guild_id}/bans", guild_id=guild_id), params=params)

    async def next(self) -> BanEntry:
        if self.bans.empty():
            await self.fill_bans()

        try:
            return self.bans.get_nowait()
        except asyncio.QueueEmpty as e:
            raise NoMoreItems from e

    def _get_retrieve(self):
        l = self.limit
        r = 1000 if l is None or l > 1000 else l
        self.retrieve = r
        return r > 0

    async def fill_bans(self) -> None:
        if not self._get_retrieve():
            return
        before = self.before.id if self.before else None
        after = self.after.id if self.after else None
        data = await self.get_bans(self.guild.id, self.retrieve, before, after)
        if not data:
            # no data, terminate
            return

        if len(data) < 1000:
            self.limit = 0  # terminate loop

        self.after = Object(id=int(data[-1]["user"]["id"]))

        for element in reversed(data):
            await self.bans.put(self.create_ban(element))

    def create_ban(self, data):
        return BanEntry(reason=data["reason"], user=self.state.store_user(data=data["user"]))
