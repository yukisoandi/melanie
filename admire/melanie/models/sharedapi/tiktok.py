from __future__ import annotations

from melanie import BaseModel


class TikTokUserProfileResponse(BaseModel, extra="allow"):
    avatar_url: str | None
    digg_count: int | None
    follower_count: int | None
    following_count: int | None
    heart: int | None
    unique_id: str | None
    id: str | None
    nickname: str | None
    private_account: bool | None
    verified: bool | None
    video_count: int | None
    signature: str | None
