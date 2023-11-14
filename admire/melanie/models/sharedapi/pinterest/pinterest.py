from __future__ import annotations

from melanie import BaseModel


class PinterestProfileResponse(BaseModel):
    username: str
    description: str | None
    followers: int | None
    following: int | None
    pins: int | None
    url: str | None
    avatar_url: str | None


class WeHeartItProfileResponse(BaseModel):
    username: str
    description: str | None
    followers: int | None
    following: int | None
    hearts: int | None
    posts: int | None
    url: str | None
    avatar_url: str | None
