from __future__ import annotations

from typing import Optional

from melanie import BaseModel


class ChannelSettings(BaseModel):
    channel_owner: int = None
    channel_limit: int = None
    locked_channel: bool = False
    music_only: bool = False
    channel_permits: list = []
    channel_rejects: list = []


class ModelItem(BaseModel):
    id: Optional[str]
    name: Optional[str]
    custom: Optional[bool]
    deprecated: Optional[bool]
    optimal: Optional[bool]


class GuildSettings(BaseModel):
    voice_category: int = None
    join_channel: int = None
    channel_limit: int = None


class MemberSettings(BaseModel):
    channel_limit: str = None
    channel_name: str = None
    default_lock: bool = False
    permits: list = []
    default_region: str = None
    rejects: list = []
    new_notif: bool = False


class VoiceRegion(BaseModel):
    id: Optional[str]
    name: Optional[str]
    custom: Optional[bool]
    deprecated: Optional[bool]
    optimal: Optional[bool]
