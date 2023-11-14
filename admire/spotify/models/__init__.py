from __future__ import annotations

from typing import Any, Optional

import discord
import tekore as tk

from melanie import BaseModel, get_redis
from melanie.models import Field

requesting_scopes = [
    "user-read-private",
    "user-top-read",
    "user-read-recently-played",
    "user-follow-read",
    "user-library-read",
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-read-playback-position",
    "playlist-read-collaborative",
    "playlist-read-private",
    "user-follow-modify",
    "user-library-modify",
    "user-modify-playback-state",
    "playlist-modify-public",
    "playlist-modify-private",
    "ugc-image-upload",
]


class BasicMeInfo(BaseModel):
    id: Optional[int]
    username: Optional[str]
    avatar: Optional[str]
    avatar_decoration: Optional[Any]
    discriminator: Optional[str]
    public_flags: Optional[int]
    flags: Optional[int]
    banner: Optional[Any]
    banner_color: Optional[str]
    accent_color: Optional[int]
    locale: Optional[str]
    mfa_enabled: Optional[bool]
    premium_type: Optional[int]
    email: Optional[str]
    verified: Optional[bool]


class TekoreTokenDict(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int
    scope: str
    uses_pkce: Optional[bool]
    token_type: Optional[str]


class SpotifyStateInfo(BaseModel):
    state: str
    user_id: int
    init_key: str
    rebound_url: str
    exchange_key: str

    @classmethod
    def generate_new(cls, state: str, user: discord.User, channel_id: int = None, guild_id: int = None) -> SpotifyStateInfo:
        return cls(
            state=state,
            user_id=user.id,
            init_key=f"sp_exchange_{state}_{user.id}_init",
            rebound_url=f"https://discord.com/channels/{guild_id}/{channel_id}" if channel_id and guild_id else None,
            exchange_key=f"sp_exchange_{state}_{user.id}",
        )

    @classmethod
    async def from_init_key(cls, user_id, state) -> Optional[SpotifyStateInfo]:
        redis = get_redis()
        key = f"sp_exchange_{state}_{user_id}_init"
        data = await redis.get(key)
        if data:
            return cls.parse_raw(data)


class SpotifyStateHolder(BaseModel):
    auth: tk.UserAuth = Field(None, exclude=True)
    init_state: str
    rebound_url: Optional[str]
    info: BasicMeInfo
    token: Optional[TekoreTokenDict]

    @property
    def cache_key(self) -> str:
        return f"sp_exchange_{self.init_state}_{self.info.id}"

    async def save_redis(self) -> None:
        redis = get_redis()

        await redis.set(self.cache_key, self.json(), ex=60)
