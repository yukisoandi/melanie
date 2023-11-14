from __future__ import annotations

from typing import Optional

from melanie import BaseModel


class MemberSettings(BaseModel):
    jailed_roles = []


class ModlogMessage(BaseModel):
    channel_id: Optional[int]
    message_id: Optional[int]


class XItem(BaseModel):
    time: Optional[int]
    level: Optional[int]
    roles: Optional[list]
    author: Optional[int]
    reason: Optional[Optional[str]]
    duration: Optional[Optional[float]]
    modlog_message: Optional[ModlogMessage]
