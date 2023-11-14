from __future__ import annotations

import threading

from runtimeopt import offloaded

from .base import BaseModel, Field

yt = threading.local()


class Fragment(BaseModel):
    url: str | None
    duration: float | None


class HttpHeaders(BaseModel):
    user__agent: str | None = Field(None, alias="User-Agent")
    accept: str | None = Field(None, alias="Accept")
    accept__language: str | None = Field(None, alias="Accept-Language")
    sec__fetch__mode: str | None = Field(None, alias="Sec-Fetch-Mode")


class DownloaderOptions(BaseModel):
    http_chunk_size: int | None


class Format(BaseModel):
    format_id: str | None
    format_note: str | None
    ext: str | None
    protocol: str | None
    acodec: str | None
    vcodec: str | None
    url: str | None
    width: int | None
    height: int | None
    fps: float | None
    rows: int | None
    columns: int | None
    fragments: list[Fragment] | None
    audio_ext: str | None
    video_ext: str | None
    format: str | None
    resolution: str | None
    http_headers: HttpHeaders | None
    asr: int | None
    filesize: int | None
    source_preference: int | None
    audio_channels: int | None
    quality: int | None
    has_drm: bool | None
    tbr: float | None
    language: str | None
    language_preference: int | None
    preference: int | None
    dynamic_range: str | None
    abr: float | None
    downloader_options: DownloaderOptions | None
    container: str | None
    vbr: float | None
    filesize_approx: int | None


class Thumbnail(BaseModel):
    url: str | None
    preference: int | None
    id: str | None
    height: int | None
    width: int | None
    resolution: str | None


class LiveChat(BaseModel):
    url: str | None
    video_id: str | None
    ext: str | None
    protocol: str | None


class Subtitles(BaseModel):
    live_chat: list[LiveChat] | None


class DownloaderOptions1(DownloaderOptions):
    pass


class HttpHeaders1(HttpHeaders):
    pass


class SearchResult(BaseModel):
    id: str | None
    title: str | None
    formats: list[Format] | None
    thumbnails: list[Thumbnail] | None
    thumbnail: str | None
    description: str | None
    uploader: str | None
    uploader_id: str | None
    uploader_url: str | None
    channel_id: str | None
    channel_url: str | None
    duration: int | None
    view_count: int | None
    average_rating: str | None
    age_limit: int | None
    webpage_url: str | None
    categories: list[str] | None
    tags: list[str] | None
    playable_in_embed: bool | None
    is_live: bool | None
    was_live: bool | None
    live_status: str | None
    release_timestamp: int | None
    automatic_captions: dict[str, str] | None
    subtitles: Subtitles | None
    comment_count: str | None
    chapters: str | None
    like_count: int | None
    channel: str | None
    channel_follower_count: int | None
    upload_date: str | None
    availability: str | None
    original_url: str | None
    webpage_url_basename: str | None
    webpage_url_domain: str | None
    extractor: str | None
    extractor_key: str | None
    playlist_count: int | None
    playlist: str | None
    playlist_id: str | None
    playlist_title: str | None
    playlist_uploader: str | None
    playlist_uploader_id: str | None
    n_entries: int | None
    playlist_index: int | None
    __last_playlist_index: int | None  # type:ignore
    playlist_autonumber: int | None
    display_id: str | None
    fulltitle: str | None
    duration_string: str | None
    release_date: str | None
    requested_subtitles: str | None
    _has_drm: str | None
    asr: int | None
    filesize: int | None
    format_id: str | None
    format_note: str | None
    source_preference: int | None
    fps: str | None
    audio_channels: int | None
    height: str | None
    quality: int | None
    has_drm: bool | None
    tbr: float | None
    url: str | None
    width: str | None
    language: str | None
    language_preference: int | None
    ext: str | None
    vcodec: str | None
    acodec: str | None
    abr: float | None
    downloader_options: DownloaderOptions1 | None
    container: str | None
    protocol: str | None
    audio_ext: str | None
    video_ext: str | None
    format: str | None
    resolution: str | None
    http_headers: HttpHeaders1 | None


class YoutubeSearchResults(BaseModel):
    results: list[SearchResult]


class YTSearchRequest(BaseModel):
    query: str


@offloaded
def do_search(query: str) -> bytes:
    import orjson
    import yt_dlp

    with yt_dlp.YoutubeDL({"format": "bestaudio", "clean_infojson": True, "quiet": True, "simulate": True}) as ydl:
        data = ydl.extract_info(f"ytsearch:{query}", download=False, process=False)["entries"]
        items = []
        for record in data:
            if len(items) > 2:
                break
            else:
                items.append(record)
        final = {"results": items}
        return orjson.dumps(final)
