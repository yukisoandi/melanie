from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import time
from typing import Any, Optional

import xxhash

from melanie import BaseModel as _BaseModel
from melanie.helpers import get_image_colors2
from melanie.models import Field
from melanie.models.colors import ColorPalette

from .profile_model import DiscordUser, ProfileModel

try:
    import discord
except ImportError:
    discord = None


class BaseModel(_BaseModel, extra="allow"):
    pass


class InvalidSignature(Exception):
    pass


class MelanieEmoji(BaseModel):
    name: Optional[str]
    id: Optional[int]
    animated: Optional[bool]
    url: Optional[str]
    dispaly_name: Optional[str]


class ActivityAsset(BaseModel):
    small_text: Optional[str]
    large_text: Optional[str]

    small_image: Optional[str]
    large_image: Optional[str]


class SpotifyData(BaseModel):
    album_cover_url: Optional[str]
    title: Optional[str] = Field(None, alias="tile")
    artist: Optional[Any]
    album: Optional[str]
    track_id: Optional[str]
    start: Optional[int]
    end: Optional[int]
    duration: Optional[float]


class MelanieActivity(BaseModel):
    name: Optional[str]
    primary: bool = False
    emoji: Optional[MelanieEmoji]
    created_at: Optional[int] = time.time()
    url: Optional[str]

    assets: Optional[ActivityAsset]
    spotify_data: Optional[SpotifyData]
    state: Optional[str]
    flags: Optional[int]
    type: Optional[int]
    color: Optional[ColorPalette]
    application_id: Optional[int]
    session_id: Optional[str]

    async def get_colors(self) -> Optional[ColorPalette]:
        if self.color:
            return

        assets = self.assets
        if self.spotify_data and self.spotify_data.album_cover_url:
            img_url = self.spotify_data.album_cover_url
        elif assets and assets.large_image:
            img_url = assets.large_image
        elif assets and assets.small_image:
            img_url = assets.small_image
        else:
            img_url = None
        if not img_url:
            return None
        async with asyncio.timeout(10):
            self.color = await get_image_colors2(img_url)
            return self.color


def serialize_activites(activities: list[discord.Activity]) -> list[MelanieActivity]:
    out = []

    for idx, ac in enumerate(activities):
        act_out = MelanieActivity(**ac.to_dict())
        if hasattr(ac, "application_id"):
            act_out.application_id = ac.application_id
        if hasattr(ac, "url"):
            act_out.url = ac.url

        if hasattr(ac, "session_id"):
            act_out.session_id = str(ac.session_id) if ac.session_id else None

        act_out.type = int(ac.type)

        if hasattr(ac, "assets"):
            act_out.assets = ActivityAsset(**ac.assets)

        act_out.primary = idx == 0

        if hasattr(ac, "small_image_url"):
            act_out.assets.small_image = ac.small_image_url

        if hasattr(ac, "large_image_url"):
            act_out.assets.large_image = ac.large_image_url
        if hasattr(ac, "small_image_text"):
            act_out.assets.small_text = ac.small_image_text

        if hasattr(ac, "large_image_text"):
            act_out.assets.large_text = ac.large_image_text

        if isinstance(ac, discord.Spotify):
            ac: discord.Spotify

            d = SpotifyData(album_cover_url=ac.album_cover_url, title=ac.title, artist=ac.artist, album=ac.album, track_id=ac.track_id)

            with contextlib.suppress(KeyError):
                d.duration = ac.duration.total_seconds()
            with contextlib.suppress(KeyError):
                d.start = ac.start.timestamp()
            with contextlib.suppress(KeyError):
                d.end = ac.end.timestamp()
            act_out.spotify_data = d

        if isinstance(ac, discord.CustomActivity) and ac.emoji:
            act_out.emoji = MelanieEmoji(name=ac.emoji.name, id=ac.emoji.id, display_name=str(ac.emoji), animated=ac.emoji.animated, url=str(ac.emoji.url))

        out.append(act_out)

    return out


class GatewayUserStatus(BaseModel):
    primary: Optional[str]

    desktop: Optional[str]

    mobile: Optional[str]

    web: Optional[str]

    @classmethod
    def from_member(cls, member: discord.Member) -> GatewayUserStatus:
        cls = cls()
        cls.primary = str(member.activity)
        cls.mobile = str(member.mobile_status)
        cls.desktop = str(member.desktop_status)
        cls.web = str(member.web_status)
        return cls


class CachedGatewayUser(BaseModel):
    username: str
    id: int
    discriminator: str
    public_flags: Any
    activities: Optional[list[MelanieActivity]]
    status: Optional[GatewayUserStatus]
    worker_ident: Optional[str]

    @staticmethod
    def make_key(user_id) -> str:
        return f"tessacache:{xxhash.xxh32_hexdigest(user_id)}"

    @staticmethod
    def make_seen_key(worker_name, user_id) -> str:
        return f"seen:{xxhash.xxh32_hexdigest(worker_name, user_id)}"


def sign_payload(key, payload: bytes) -> str:
    if isinstance(payload, str):
        payload = payload.encode("UTF-8")

    auth = hmac.new(key=key.encode("UTF-8"), digestmod=hashlib.sha3_256)

    auth.update(payload)

    return auth.hexdigest()


class BioRequest(BaseModel):
    user_id: int
    guild_id: Optional[int]
    sig: Optional[str]
    req_user_id: Optional[int]
    timestamp: Optional[int]

    @property
    def request_key(self) -> str:
        prekey = f"{self.user_id}{self.guild_id}"
        return f"tessabio:{xxhash.xxh32_hexdigest(prekey)}"

    @property
    def cache_key(self) -> Optional[str]:
        return f"tessabio:{xxhash.xxh32_hexdigest(self.sig)}" if self.sig else None

    @property
    def event_key(self) -> Optional[str]:
        return f"tessaevent:{xxhash.xxh32_hexdigest(self.sig)}" if self.sig else None


class BannerType(BaseModel):
    hash: Optional[str] = Field(None, alias="banner")
    url: Optional[str]
    color: Optional[ColorPalette]
    guild_id: Optional[int]
    format: Optional[str]
    user_id: Optional[int]

    # https://cdn.discordapp.com/banners/728095627757486081/806c6ce3c43d2ab26c6349060d7161b3.png?size=1024
    async def get_color(self) -> Optional[ColorPalette]:
        if self.color:
            return
        self.color = await get_image_colors2(self.url)
        return self.color

    def set_urls(self):
        self.format = "gif" if self.hash.startswith("a_") else "png"

        if self.guild_id:
            self.url = f"https://cdn.discordapp.com/guilds/{self.guild_id}/users/{self.user_id}/banners/{self.hash}.{self.format}?size=1024"

        else:
            self.url = f"https://cdn.discordapp.com/banners/{self.user_id}/{self.hash}.{self.format}?size=1024"

    @classmethod
    def from_hash(cls, user_id: int, banner_hash: str, guild_id: Optional[int]) -> BannerType:
        cls = cls(hash=banner_hash, guild_id=guild_id, user_id=user_id)
        cls.set_urls()

        return cls


class BioUser(DiscordUser):
    bio: Optional[str]
    banner: Optional[BannerType]


class BioMember(DiscordUser):
    bio: Optional[str]
    banner: Optional[BannerType]


class APIBioRequest(BaseModel):
    user_id: int
    guild_id: Optional[int]
    sig: Optional[str]

    @property
    def request_key(self) -> str:
        prekey = f"{self.user_id}{self.guild_id}"
        return f"tessabio:{xxhash.xxh32_hexdigest(prekey)}"

    @property
    def cache_key(self) -> Optional[str]:
        return f"tessabio:{xxhash.xxh32_hexdigest(self.sig)}" if self.sig else None

    @property
    def event_key(self) -> Optional[str]:
        return f"tessaevent:{xxhash.xxh32_hexdigest(self.sig)}" if self.sig else None


class BioResponse(BaseModel):
    user: Optional[BioUser]
    profile_data: Optional[ProfileModel]
    member: Optional[BioMember]
    activities: Optional[list[Any]]
    request: Optional[BioRequest]
    status: Optional[GatewayUserStatus]
    sig: Optional[str]
