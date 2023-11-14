from __future__ import annotations

from melanie import BaseModel


class GuildSettings(BaseModel):
    voice_category: int = None
    join_channel: int = None
    channel_limit: int = None


class MemberSettings(BaseModel):
    channel_limit: str = None
    channel_name: str = None
    default_lock: bool = False
    permits: list = []
    rejects: list = []
    new_notif: bool = False


class ChannelSettings(BaseModel):
    channel_owner: int = None
    channel_limit: int = None
