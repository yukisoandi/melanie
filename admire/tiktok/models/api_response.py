from __future__ import annotations

import textwrap

import arrow
import discord
from boltons.strutils import find_hashtags

from melanie import BaseModel, Field, get_filename_from_url, intword


class VideoIcon(BaseModel):
    url_list: list[str] | None
    width: int | None = Field(None, alias="image_width")
    height: int | None = Field(None, alias="image_height")


class TiktokPostRequest(BaseModel):
    url: str | None


class Author(BaseModel):
    avatar_thumb: str | None
    unique_id: str | None


class AuthorStats(BaseModel):
    follower_count: int | None
    following_count: int | None
    heart: int | None
    digg_count: int | None
    video_count: int | None


class CoverLarge(BaseModel):
    pass


class Statistics(BaseModel):
    digg_count: int = 0
    play_count: int = 0
    share_count: int = 0
    comment_count: int = 0


class Video(BaseModel):
    ratio: str | None
    height: int | None
    width: int | None
    dynamic_cover: CoverLarge | None
    origin_cover: CoverLarge | None
    duration: int | None


class Image(BaseModel):
    display_image: VideoIcon | None


class Music(BaseModel):
    duration: int | None
    album: str | None
    id: str | None
    title: str | None


class ImagePostInfo(BaseModel):
    images: list[Image] | None


class TikTokVideoResponse(BaseModel):
    aweme_id: str | None
    avatar_bytes: bytes | None
    video_url: str | None
    author_id: str | None
    author: Author | None
    avatar_thumb: str | None
    create_time: int | None
    desc: str | None = ""
    direct_download_urls: list[str] | None
    filename: str | None
    id: str | None
    music: Music | None
    nickname: str | None
    statistics: Statistics | None
    video_bytes: bytes | None
    share_url: str | None
    video: Video | None
    cover_image_url: str | None
    image_post_info: ImagePostInfo | None
    embed_color: int | None

    @property
    def avatar_filename(self) -> str:
        return get_filename_from_url(self.avatar_thumb) if self.avatar_thumb else None

    @property
    def images(self) -> list[str]:
        return [i.display_image.url_list[-1] for i in self.image_post_info.images] if self.image_post_info else None

    def make_embed(self, requester: discord.User | None) -> discord.Embed:
        if self.desc is None:
            self.desc = ""
        self.desc = self.desc + "\n"

        hashtags = find_hashtags(self.desc)

        for h in hashtags:
            self.desc = self.desc.replace(f"#{h}", "")

        embed = discord.Embed()
        embed.title = textwrap.shorten(self.desc, 240)

        embed.url = self.share_url

        embed.timestamp = arrow.get(self.create_time).datetime

        embed.set_footer(
            text=f"\n❤️  {intword(self.statistics.digg_count)}   👀  {intword(self.statistics.play_count)}   💬  {intword(self.statistics.comment_count)}",
            icon_url="https://f002.backblazeb2.com/file/botassets/tiktok_icon.png",
        )

        if avatar_url := self.avatar_thumb or self.author.avatar_thumb:
            embed.set_author(name=f"{self.author.unique_id }", icon_url=str(avatar_url), url=f"https://www.tiktok.com/@{self.author.unique_id}")
        else:
            embed.set_author(name=f"{self.author.unique_id }", url=f"https://www.tiktok.com/@{self.author.unique_id}")
        return embed
