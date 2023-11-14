from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

import discord
from anyio import Path as AsyncPath
from xxhash import xxh32_hexdigest

from melanie import BaseModel, Field

REDDIT_HEX = "#FF5700"
TWITTER_HEX = "#1DA1F2"
YOUTUBE_HEX = "#FF0000"


class DiscordResult(BaseModel):
    embed: discord.Embed
    file: discord.File
    file_size: int


def get_cache_dir() -> Path:
    base = Path("/cache")
    if not base.exists():
        fallback = Path.home() / ".videofetch_cache"
        fallback.mkdir(exist_ok=True, parents=True)
        return fallback

    else:
        desired = base / "videofetch"
        desired.mkdir(exist_ok=True, parents=True)
    return desired


class HttpHeaders(BaseModel):
    user__agent: Optional[str] = Field(None, alias="User-Agent")
    accept: Optional[str] = Field(None, alias="Accept")
    accept__language: Optional[str] = Field(None, alias="Accept-Language")
    sec__fetch__mode: Optional[str] = Field(None, alias="Sec-Fetch-Mode")


class Format(BaseModel):
    format_id: Optional[str]
    format_index: Optional[Any]
    url: Optional[str]
    manifest_url: Optional[str]
    tbr: Optional[float]
    ext: Optional[str]
    fps: Optional[Any]
    protocol: Optional[str]
    preference: Optional[Any]
    quality: Optional[Any]
    width: Optional[int]
    height: Optional[int]
    vcodec: Optional[str]
    acodec: Optional[str]
    dynamic_range: Optional[str]
    video_ext: Optional[str]
    audio_ext: Optional[str]
    vbr: Optional[float]
    abr: Optional[float]
    format: Optional[str]
    resolution: Optional[str]
    filesize_approx: Optional[float]
    http_headers: Optional[HttpHeaders]


class Thumbnail(BaseModel):
    id: Optional[str]
    url: Optional[str]
    width: Optional[int]
    height: Optional[int]
    resolution: Optional[str]


class HttpHeaders1(HttpHeaders):
    pass


class RequestedDownload(BaseModel):
    epoch: Optional[int]
    filepath: Optional[str]
    file_size: Optional[int]

    async def to_file(self) -> tuple[discord.File, int]:
        import tuuid

        target = AsyncPath(self.filepath)
        name = f"bleedReddi{tuuid.tuuid()}.{target.suffix}"
        st_result = await target.stat()
        size = st_result.st_size
        return discord.File(io.BytesIO(await target.read_bytes()), filename=name), size


class VideoDownload(BaseModel):
    @staticmethod
    def make_key(url):
        return xxh32_hexdigest(f"ytdlp_dl:{url}")

    id: Optional[str]
    title: Optional[str]
    description: Optional[str]
    uploader: Optional[str]
    timestamp: Optional[int]
    uploader_id: Optional[str]
    uploader_url: Optional[str]
    like_count: Optional[int]
    repost_count: Optional[int]
    comment_count: Optional[int]
    age_limit: Optional[int]
    tags: Optional[list]
    formats: Optional[list[Format]]
    subtitles: Optional[dict[str, Any]]
    thumbnails: Optional[list[Thumbnail]]
    duration: Optional[float]
    webpage_url: Optional[str]
    original_url: Optional[str]
    webpage_url_basename: Optional[str]
    webpage_url_domain: Optional[str]
    extractor: Optional[str]
    extractor_key: Optional[str]
    playlist: Optional[Any]
    playlist_index: Optional[Any]
    thumbnail: Optional[str]
    display_id: Optional[str]
    fulltitle: Optional[str]
    video_bytes: Optional[bytes]
    duration_string: Optional[str]
    upload_date: Optional[str]
    requested_subtitles: Optional[Any]
    __has_drm: Optional[bool]  # type:ignore
    url: Optional[str]
    format_id: Optional[str]
    tbr: Optional[int]
    width: Optional[int]
    height: Optional[int]
    protocol: Optional[str]
    ext: Optional[str]
    video_ext: Optional[str]
    audio_ext: Optional[str]
    vbr: Optional[int]
    abr: Optional[int]
    format: Optional[str]
    resolution: Optional[str]
    dynamic_range: Optional[str]
    filesize_approx: Optional[float]
    http_headers: Optional[HttpHeaders1]
    requested_downloads: Optional[list[RequestedDownload]]
