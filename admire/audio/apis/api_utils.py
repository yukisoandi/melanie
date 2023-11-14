from __future__ import annotations

import datetime
import json
from collections import namedtuple
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Optional, Union

import discord
import lavalink
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list

from audio.errors import InvalidPlaylistScope, MissingAuthor, MissingGuild
from audio.utils import PlaylistScope


def _(x):
    return x


@dataclass
class YouTubeCacheFetchResult:
    query: Optional[str]
    last_updated: int

    def __post_init__(self) -> None:
        if isinstance(self.last_updated, int):
            self.updated_on: datetime.datetime = datetime.datetime.fromtimestamp(self.last_updated)


@dataclass
class SpotifyCacheFetchResult:
    query: Optional[str]
    last_updated: int

    def __post_init__(self) -> None:
        if isinstance(self.last_updated, int):
            self.updated_on: datetime.datetime = datetime.datetime.fromtimestamp(self.last_updated)


@dataclass
class LavalinkCacheFetchResult:
    query: Optional[MutableMapping]
    last_updated: int

    def __post_init__(self) -> None:
        if isinstance(self.last_updated, int):
            self.updated_on: datetime.datetime = datetime.datetime.fromtimestamp(self.last_updated)

        if isinstance(self.query, str):
            self.query = json.loads(self.query)


@dataclass
class LavalinkCacheFetchForGlobalResult:
    query: str
    data: MutableMapping

    def __post_init__(self) -> None:
        if isinstance(self.data, str):
            self.data_string = str(self.data)
            self.data = json.loads(self.data)


@dataclass
class PlaylistFetchResult:
    playlist_id: int
    playlist_name: str
    scope_id: int
    author_id: int
    playlist_url: Optional[str] = None
    tracks: list[MutableMapping] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.tracks, str):
            self.tracks = json.loads(self.tracks)


@dataclass
class QueueFetchResult:
    guild_id: int
    room_id: int
    track: dict = field(default_factory=lambda: {})
    track_object: lavalink.Track = None

    def __post_init__(self) -> None:
        if isinstance(self.track, str):
            self.track = json.loads(self.track)
        if self.track:
            self.track_object = lavalink.Track(self.track)


def standardize_scope(scope: str) -> str:
    """Convert any of the used scopes into one we are expecting."""
    scope = scope.upper()
    valid_scopes = ["GLOBAL", "GUILD", "AUTHOR", "USER", "SERVER", "MEMBER", "BOT"]

    if scope in PlaylistScope.list():
        return scope
    elif scope not in valid_scopes:
        msg = f'"{scope}" is not a valid playlist scope. Scope needs to be one of the following: {humanize_list(valid_scopes)}'
        raise InvalidPlaylistScope(msg)

    if scope in {"GLOBAL", "BOT"}:
        scope = PlaylistScope.GLOBAL.value
    elif scope in ["GUILD", "SERVER"]:
        scope = PlaylistScope.GUILD.value
    elif scope in ["USER", "MEMBER", "AUTHOR"]:
        scope = PlaylistScope.USER.value

    return scope


def prepare_config_scope(bot: Melanie, scope, author: Union[discord.abc.User, int] = None, guild: Union[discord.Guild, int] = None):
    """Return the scope used by Playlists."""
    scope = standardize_scope(scope)
    if scope == PlaylistScope.GLOBAL.value:
        return [PlaylistScope.GLOBAL.value, bot.user.id]
    elif scope == PlaylistScope.USER.value:
        if author is None:
            msg = "Invalid author for user scope."
            raise MissingAuthor(msg)
        return [PlaylistScope.USER.value, int(getattr(author, "id", author))]
    else:
        if guild is None:
            msg = "Invalid guild for guild scope."
            raise MissingGuild(msg)
        return [PlaylistScope.GUILD.value, int(getattr(guild, "id", guild))]


def prepare_config_scope_for_migration23(
    scope,
    author: Union[discord.abc.User, int] = None,
    guild: discord.Guild = None,
):  # TODO: remove me in a future version ?
    """Return the scope used by Playlists."""
    scope = standardize_scope(scope)

    if scope == PlaylistScope.GLOBAL.value:
        return [PlaylistScope.GLOBAL.value]
    elif scope == PlaylistScope.USER.value:
        if author is None:
            msg = "Invalid author for user scope."
            raise MissingAuthor(msg)
        return [PlaylistScope.USER.value, str(getattr(author, "id", author))]
    else:
        if guild is None:
            msg = "Invalid guild for guild scope."
            raise MissingGuild(msg)
        return [PlaylistScope.GUILD.value, str(getattr(guild, "id", guild))]


FakePlaylist = namedtuple("Playlist", "author scope")
