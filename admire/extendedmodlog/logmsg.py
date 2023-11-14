from __future__ import annotations

from contextlib import suppress
from typing import Optional

import orjson

from melanie import BaseModel, get_redis


class Author(BaseModel):
    name: str
    icon_url: str


class EmbedField(BaseModel):
    name: str
    value: str
    inline: str


class Embed(BaseModel):
    description: str
    timestamp: str
    color: Optional[int]
    author: Author
    fields: list[EmbedField]


class LoggerAuthor(BaseModel):
    id: int
    username: str
    discriminator: str
    avatar: Optional[str]


class MelanieCachedMessage(BaseModel):
    id: str
    bot: bool
    author_id: str
    author_name: str
    content: Optional[str]
    channel_id: str
    avatar: Optional[str]
    timestamp: Optional[str]
    timestamp2: int
    embed_raw: Optional[str]
    guild_id: Optional[str]

    @staticmethod
    def key(id: str):
        return f"logmsg3:{id}"

    @property
    def embeds(self) -> list:
        return orjson.loads(self.embed_raw) if self.embed_raw else None

    @property
    def author(self) -> LoggerAuthor:
        return LoggerAuthor(
            id=int(self.author_id),
            username=str(self.author_name),
            discriminator=str(self.author_name).split("#")[1],
            avatar=str(self.avatar) if self.avatar else None,
        )

    @classmethod
    async def find_by_id(cls, id: int | str):
        redis = get_redis()
        data = await redis.hgetall(cls.key(id))
        if data:
            data = {k.decode(): v for k, v in data.items()}
            return cls.parse_obj(data)

    def serialize_for_logger(self) -> dict:
        final = self.dict()
        final["author"] = self.author.dict()
        final["embeds"] = []
        discards = ["embed_raw", "timestamp2", "bot", "guild_id", "author_name", "author_id"]

        for a in discards:
            with suppress(AttributeError):
                del final[a]

        return final
