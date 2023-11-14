from __future__ import annotations

import os
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha1
from threading import RLock
from typing import Union

import orjson
import tuuid
from cashews import Cache
from loguru import logger as log
from redis.asyncio import BlockingConnectionPool
from redis.asyncio.client import (
    AsyncPubsubWorkerExceptionHandler as _AsyncPubsubWorkerExceptionHandler,
)
from redis.asyncio.client import Pipeline as _Pipeline
from redis.asyncio.client import PubSub, Redis
from redis.asyncio.lock import Lock
from redis.client import Redis as SyncRedis
from redis.commands.json import JSON as _JSON
from redis.commands.json.commands import JSONCommands
from redis.connection import parse_url
from redis.exceptions import NoScriptError

from tair.commands import TairHashCommands

try:
    GLOBAL_REDIS  # type: ignore
except NameError:
    GLOBAL_REDIS = {}

REDIS_URL: str = os.environ["REDIS_URL"]

INCREMENT_SCRIPT = b"""
    local current
    current = tonumber(redis.call("incrby", KEYS[1], ARGV[2]))
    if current == tonumber(ARGV[2]) then
        redis.call("expire", KEYS[1], ARGV[1])
    end
    return current
"""

INCREMENT_SCRIPT_HASH: str = sha1(INCREMENT_SCRIPT).hexdigest()


class FieldValueItem:
    def __init__(self, field: bytes, value: bytes) -> None:
        self.field = field
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FieldValueItem):
            return False
        return self.field == other.field and self.value == other.value

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __repr__(self) -> str:
        return f"{{field: {self.field.decode()}, value: {self.value.decode()}}}"


class AsyncPubsubWorkerExceptionHandler(_AsyncPubsubWorkerExceptionHandler):
    async def __call__(self, e: BaseException, pubsub: PubSub):
        log.opt(exception=e).exception("Pubsub error for {}", pubsub)


_lock = RLock()


def get_redis() -> MelanieRedis:
    return GLOBAL_REDIS.get("client")


async def fetch_redis():
    from melanie.redis import MelanieRedis, get_redis

    with _lock:
        redis = get_redis() or await MelanieRedis.from_url()

        return redis


class AsyncJSONPipeline(JSONCommands, _Pipeline):
    """Pipeline for the module."""


class ORJSONDecoder:
    def __init__(self, **kwargs) -> None:
        # eventually take into consideration when deserializing
        self.options = kwargs

    def decode(self, obj: Union[bytearray, bytes, memoryview, str]):
        return orjson.loads(obj)


class ORJSONEncoder:
    def __init__(self, **kwargs) -> None:
        # eventually take into consideration when serializing
        self.options = kwargs

    def encode(self, obj) -> str:
        # decode back to str, as orjson returns bytes
        return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")


class JSON(_JSON):
    def pipeline(self, transaction=True, shard_hint=None):
        """Creates a pipeline for the JSON module, that can be used for executing
        JSON commands, as well as classic core commands.

        Usage example:

        r = redis.Redis()
        pipe = r.json().pipeline()
        pipe.jsonset('foo', '.', {'hello!': 'world'})
        pipe.jsonget('foo')
        pipe.jsonget('notakey')
        """
        p = AsyncJSONPipeline(
            connection_pool=self.client.connection_pool,
            response_callbacks=self._MODULE_CALLBACKS,
            transaction=transaction,
            shard_hint=shard_hint,
        )

        p._encode = self._encode
        p._decode = self._decode
        return p


@contextmanager
def blocking_redis() -> Iterator[SyncRedis]:
    """Borrow a Sync redis instance.

    Must be used as a context manager!

    """
    REDIS_URL = "redis://melanie.melaniebot.net"

    redis = SyncRedis(single_connection_client=True, **parse_url(REDIS_URL))
    try:
        yield redis
    finally:
        redis.close()


rcache: Cache = Cache()


class MelanieRedis(Redis, TairHashCommands):
    def json(self, encoder=ORJSONEncoder(), decoder=ORJSONDecoder()):
        """Access the json namespace, providing support for redis json."""
        return JSON(client=self, encoder=encoder, decoder=decoder)

    # async def exhget(self, key: KeyT, field: FieldT) -> ResponseT:

    async def exhgetall(self, key: str) -> list[FieldValueItem]:
        resp = await super().exhgetall(key)
        return [FieldValueItem(resp[i], resp[i + 1]) for i in range(0, len(resp), 2)]

    async def __aenter__(self) -> MelanieRedis:
        return await self.initialize()

    async def __aexit__(self, *e) -> None:
        log.warning("Shutting down our Redis.")
        await self.close()

    async def close(self):
        return await self.aclose(close_connection_pool=True)

    @classmethod
    async def from_url(cls, url: str = REDIS_URL, **kwargs) -> "MelanieRedis":
        from melanie.redis import GLOBAL_REDIS

        if "client" in GLOBAL_REDIS:
            await GLOBAL_REDIS["client"].close()

            del GLOBAL_REDIS["client"]
        connection_pool = BlockingConnectionPool.from_url(url, **kwargs)
        client = cls(
            connection_pool=connection_pool,
            single_connection_client=False,
        )

        auto_close_connection_pool = None
        if auto_close_connection_pool is not None:
            warnings.warn(
                DeprecationWarning(
                    '"auto_close_connection_pool" is deprecated '
                    "since version 5.0.0. "
                    "Please create a ConnectionPool explicitly and "
                    "provide to the Redis() constructor instead.",
                ),
            )
        else:
            auto_close_connection_pool = True
        client.auto_close_connection_pool = auto_close_connection_pool

        GLOBAL_REDIS["client"] = client
        GLOBAL_REDIS["created"] = time.time()

        log.success("New Redis instance @ {} is CONNECTED.", id(client))

        log.warning("via {}", client.connection_pool.connection_class.__name__)
        dur = 0
        for _ in range(10):
            start = time.perf_counter()
            await client.ping()
            dur += time.perf_counter() - start

        avg = (dur / 10) * 1000000

        log.success("Current latency: {} microseconds", round(avg, 3))

        return GLOBAL_REDIS["client"]

    async def ratelimited(self, resource_ident: str, request_limit: int, timespan: int = 60, increment: int = 1) -> bool:
        key = f"rl:{resource_ident}"
        try:
            current_usage = await self.evalsha(INCREMENT_SCRIPT_HASH, 1, key, timespan, increment)
        except NoScriptError:
            current_usage = await self.eval(INCREMENT_SCRIPT, 1, key, timespan, increment)
        return bool(int(current_usage) > request_limit)

    def get_lock(
        self,
        name: str = None,
        timeout: float = 500.0,
        sleep: float = 0.1,
        blocking: bool = True,
        blocking_timeout: float = None,
        thread_local=True,
    ) -> Lock:
        if not name:
            name = tuuid.tuuid()
        name = f"lock:{name}"
        return self.lock(name=name, timeout=timeout, sleep=sleep, blocking=blocking, blocking_timeout=blocking_timeout, thread_local=thread_local)
