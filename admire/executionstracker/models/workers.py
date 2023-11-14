from __future__ import annotations

from typing import Optional

from melanie import BaseModel


class Channel(BaseModel):
    name: Optional[str]
    id: Optional[int]


class Guild(BaseModel):
    name: Optional[str]
    id: Optional[int]
    member_count: Optional[int]
    joined_on: Optional[int]
    channels: Optional[dict[str, Channel]]


class WorkerRegistration(BaseModel):
    name: Optional[str]
    id: Optional[int]
    guild_count: Optional[int]
    channel_count: Optional[int]
    users: Optional[int]
    ts: Optional[int]
    guilds: Optional[dict[str, Guild]]
