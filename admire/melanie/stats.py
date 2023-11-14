from __future__ import annotations

import datetime

try:
    from typing import Optional

    import orjson

    from .core import BaseModel

except Exception:
    pass


STR_NUMBER_TO_INT_COLS = ["user_id", "guild_id", "channel_id"]


class CachedStatsMessage(BaseModel):
    content: Optional[str]
    created_at: datetime.datetime
    user_name: str
    user_id: int
    guild_name: Optional[str]
    guild_id: int
    user_nick: Optional[str]
    channel_name: str
    channel_id: int
    message_id: int
    _attachments_raw: Optional[str]
    reference: Optional[int]

    @property
    def attachments(self):
        return orjson.loads(self._attachments_raw) if self._attachments_raw else None

    @classmethod
    async def fetch_from_id(cls, stats: MelanieStatsPool, message_id: int) -> Optional[CachedStatsMessage]:
        if not stats:
            return None
        data: list[CachedStatsMessage] = await stats.submit_query(
            f"select content ,created_at ,user_name ,user_id ,guild_name ,guild_id ,user_nick ,channel_name ,channel_id ,message_id ,attachments _attachments_raw ,reference from guild_messages where message_id = {message_id} ",
            result_model=CachedStatsMessage,
        )
        return data[0] if data else None


class NoStatsPool(Exception):
    pass


class MelanieStatsPool:
    pass


MelanieDataPool = MelanieStatsPool
