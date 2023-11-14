from __future__ import annotations

import concurrent
import json
import time
from types import SimpleNamespace
from typing import Union

import lavalink
from melaniebot.core import Config
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import Cog
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.dbtools import APSWConnectionWrapper

from audio.audio_logging import debug_exc_log
from audio.sql_statements import (
    PERSIST_QUEUE_BULK_PLAYED,
    PERSIST_QUEUE_CREATE_INDEX,
    PERSIST_QUEUE_CREATE_TABLE,
    PERSIST_QUEUE_DELETE_SCHEDULED,
    PERSIST_QUEUE_DROP_TABLE,
    PERSIST_QUEUE_FETCH_ALL,
    PERSIST_QUEUE_PLAYED,
    PERSIST_QUEUE_UPSERT,
    PRAGMA_FETCH_user_version,
    PRAGMA_SET_journal_mode,
    PRAGMA_SET_read_uncommitted,
    PRAGMA_SET_temp_store,
    PRAGMA_SET_user_version,
)
from melanie import log

from .api_utils import QueueFetchResult


def _(x):
    return x


class QueueInterface:
    def __init__(self, bot: Melanie, config: Config, conn: APSWConnectionWrapper, cog: Union[Audio, Cog]) -> None:  # type: ignore
        self.bot = bot
        self.database = conn
        self.config = config
        self.cog = cog
        self.statement = SimpleNamespace()
        self.statement.pragma_temp_store = PRAGMA_SET_temp_store
        self.statement.pragma_journal_mode = PRAGMA_SET_journal_mode
        self.statement.pragma_read_uncommitted = PRAGMA_SET_read_uncommitted
        self.statement.set_user_version = PRAGMA_SET_user_version
        self.statement.get_user_version = PRAGMA_FETCH_user_version
        self.statement.create_table = PERSIST_QUEUE_CREATE_TABLE
        self.statement.create_index = PERSIST_QUEUE_CREATE_INDEX

        self.statement.upsert = PERSIST_QUEUE_UPSERT
        self.statement.update_bulk_player = PERSIST_QUEUE_BULK_PLAYED
        self.statement.delete_scheduled = PERSIST_QUEUE_DELETE_SCHEDULED
        self.statement.drop_table = PERSIST_QUEUE_DROP_TABLE

        self.statement.get_all = PERSIST_QUEUE_FETCH_ALL
        self.statement.get_player = PERSIST_QUEUE_PLAYED

    async def init(self) -> None:
        """Initialize the PersistQueue table."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.database.cursor().execute, self.statement.pragma_temp_store)
            executor.submit(self.database.cursor().execute, self.statement.pragma_journal_mode)
            executor.submit(self.database.cursor().execute, self.statement.pragma_read_uncommitted)
            executor.submit(self.database.cursor().execute, self.statement.create_table)
            executor.submit(self.database.cursor().execute, self.statement.create_index)

    async def fetch_all(self) -> list[QueueFetchResult]:
        """Fetch all playlists."""
        output = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            for future in concurrent.futures.as_completed([executor.submit(self.database.cursor().execute, self.statement.get_all)]):
                try:
                    row_result = future.result()
                except Exception as exc:
                    debug_exc_log(log, exc, "Failed to complete playlist fetch from database")
                    return []

        async for index, row in AsyncIter(row_result).enumerate(start=1):
            output.append(QueueFetchResult(*row))
        return output

    async def played(self, guild_id: int, track_id: str) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.database.cursor().execute, PERSIST_QUEUE_PLAYED, {"guild_id": guild_id, "track_id": track_id})

    async def delete_scheduled(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.database.cursor().execute, PERSIST_QUEUE_DELETE_SCHEDULED)

    async def drop(self, guild_id: int) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.database.cursor().execute, PERSIST_QUEUE_BULK_PLAYED, ({"guild_id": guild_id}))

    async def enqueued(self, guild_id: int, room_id: int, track: lavalink.Track) -> None:
        enqueue_time = track.extras.get("enqueue_time", 0)
        if enqueue_time == 0:
            track.extras["enqueue_time"] = int(time.time())
        track_identifier = track.track_identifier
        track = self.cog.track_to_json(track)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(
                self.database.cursor().execute,
                PERSIST_QUEUE_UPSERT,
                {"guild_id": guild_id, "room_id": room_id, "played": False, "time": enqueue_time, "track": json.dumps(track), "track_id": track_identifier},
            )
