from __future__ import annotations

from re import Pattern
from typing import Final, Optional, Union
from urllib.parse import urlparse

import discord
import regex as re
from melaniebot.core import Config
from melaniebot.core.commands import Context

from audio.audio_dataclasses import Query
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass

_RE_YT_LIST_PLAYLIST: Final[Pattern] = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.?be)(/playlist\?).*(list=)(.*)(&|$)")


class ValidationUtilities(MixinMeta, metaclass=CompositeMetaClass):
    def match_url(self, url: str) -> bool:
        try:
            query_url = urlparse(url)
            return all([query_url.scheme, query_url.netloc, query_url.path])
        except Exception:
            return False

    def match_yt_playlist(self, url: str) -> bool:
        return bool(_RE_YT_LIST_PLAYLIST.match(url))

    def is_url_allowed(self, url: str) -> bool:
        valid_tld = ["youtube.com", "youtu.be", "soundcloud.com", "bandcamp.com", "vimeo.com", "twitch.tv", "spotify.com", "localtracks"]
        query_url = urlparse(url)
        url_domain = ".".join(query_url.netloc.split(".")[-2:])
        if not query_url.netloc:
            url_domain = ".".join(query_url.path.split("/")[0].split(".")[-2:])
        return url_domain in valid_tld

    def is_vc_full(self, channel: discord.VoiceChannel) -> bool:
        return channel.user_limit != 0 and channel.user_limit <= len(channel.members)

    def can_join_and_speak(self, channel: discord.VoiceChannel) -> bool:
        current_perms = channel.permissions_for(channel.guild.me)
        return current_perms.speak and current_perms.connect

    async def is_query_allowed(self, config: Config, ctx_or_channel: Optional[Union[Context, discord.TextChannel]], query: str, query_obj: Query) -> bool:
        """Checks if the query is allowed in this server or globally."""
        if ctx_or_channel:
            guild = ctx_or_channel.guild
            ctx_or_channel.channel if isinstance(ctx_or_channel, Context) else ctx_or_channel
            query = query.lower().strip()
        else:
            guild = None
        if query_obj is not None:
            query = query_obj.lavalink_query.replace("ytsearch:", "youtubesearch").replace("scsearch:", "soundcloudsearch")
        global_whitelist = set(await config.url_keyword_whitelist())
        if global_whitelist := [i.lower() for i in global_whitelist]:
            return any(i in query for i in global_whitelist)
        global_blacklist = set(await config.url_keyword_blacklist())
        global_blacklist = [i.lower() for i in global_blacklist]
        if any(i in query for i in global_blacklist):
            return False
        if guild is not None:
            whitelist_unique: set[str] = set(await config.guild(guild).url_keyword_whitelist())
            if whitelist := [i.lower() for i in whitelist_unique]:
                return any(i in query for i in whitelist)
            blacklist_unique: set[str] = set(await config.guild(guild).url_keyword_blacklist())
            blacklist: list[str] = [i.lower() for i in blacklist_unique]
            return all(i not in query for i in blacklist)
        return True
