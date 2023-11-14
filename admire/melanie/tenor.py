from __future__ import annotations

from typing import Any, Optional

from .models.base import BaseModel


class Nanogif(BaseModel):
    preview: Optional[str] = None
    size: Optional[int] = None
    url: Optional[str] = None
    dims: Optional[list[int]] = None


class Mediumgif(BaseModel):
    dims: Optional[list[int]] = None
    preview: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None


class Nanomp4(BaseModel):
    preview: Optional[str] = None
    duration: Optional[float] = None
    size: Optional[int] = None
    dims: Optional[list[int]] = None
    url: Optional[str] = None


class Loopedmp4(BaseModel):
    preview: Optional[str] = None
    size: Optional[int] = None
    url: Optional[str] = None
    duration: Optional[float] = None
    dims: Optional[list[int]] = None


class Tinygif(BaseModel):
    url: Optional[str] = None
    preview: Optional[str] = None
    dims: Optional[list[int]] = None
    size: Optional[int] = None


class Tinymp4(BaseModel):
    url: Optional[str] = None
    preview: Optional[str] = None
    size: Optional[int] = None
    duration: Optional[float] = None
    dims: Optional[list[int]] = None


class Tinywebm(BaseModel):
    preview: Optional[str] = None
    size: Optional[int] = None
    dims: Optional[list[int]] = None
    url: Optional[str] = None


class Webm(BaseModel):
    url: Optional[str] = None
    size: Optional[int] = None
    preview: Optional[str] = None
    dims: Optional[list[int]] = None


class Gif(BaseModel):
    preview: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None
    dims: Optional[list[int]] = None


class Mp4(BaseModel):
    dims: Optional[list[int]] = None
    url: Optional[str] = None
    size: Optional[int] = None
    preview: Optional[str] = None
    duration: Optional[float] = None


class Nanowebm(BaseModel):
    url: Optional[str] = None
    preview: Optional[str] = None
    size: Optional[int] = None
    dims: Optional[list[int]] = None


class MediaItem(BaseModel):
    nanogif: Optional[Nanogif] = None
    mediumgif: Optional[Mediumgif] = None
    nanomp4: Optional[Nanomp4] = None
    loopedmp4: Optional[Loopedmp4] = None
    tinygif: Optional[Tinygif] = None
    tinymp4: Optional[Tinymp4] = None
    tinywebm: Optional[Tinywebm] = None
    webm: Optional[Webm] = None
    gif: Optional[Gif] = None
    mp4: Optional[Mp4] = None
    nanowebm: Optional[Nanowebm] = None


class Result(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    content_description: Optional[str] = None
    content_rating: Optional[str] = None
    h1_title: Optional[str] = None
    media: Optional[list[MediaItem]] = None
    bg_color: Optional[str] = None
    created: Optional[float] = None
    itemurl: Optional[str] = None
    url: Optional[str] = None
    tags: Optional[list] = None
    flags: Optional[list] = None
    shares: Optional[int] = None
    hasaudio: Optional[bool] = None
    hascaption: Optional[bool] = None
    source_id: Optional[str] = None
    composite: Optional[Any] = None


class TenorResult(BaseModel):
    results: Optional[list[Result]] = None
    next: Optional[str] = None
