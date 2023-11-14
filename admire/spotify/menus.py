from __future__ import annotations

import asyncio
import contextlib
import random
import time
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import discord
import tekore
import ujson
import xxhash
from async_lru import alru_cache
from melaniebot.core import commands
from melaniebot.core.utils.chat_formatting import box, humanize_list
from melaniebot.vendored.discord.ext import menus
from tekore.model import CurrentlyPlayingContext, Track

from melanie import footer_gif, get_redis, log, spawn_task
from melanie.helpers import get_image_colors2

from .helpers import REPEAT_STATES, SPOTIFY_LOGO, InvalidEmoji, NotPlaying, make_details

if TYPE_CHECKING:
    from spotify.spotify import Spotify as SpotifyCog


def _(x):
    return x


class EmojiHandler:
    def __init__(self) -> None:
        with open(Path(__file__).parent / "emojis.json", encoding="utf8") as infile:
            self.emojis = ujson.loads(infile.read())
            self.default = copy(self.emojis)

    def get_emoji(self, name: str, use_external: bool) -> str:
        if use_external and name in self.emojis:
            return self.emojis[name]
        return self.default[name]
        # we shouldn't have anyone deleting emoji keys

    def reload_emojis(self) -> None:
        # we could just copy default but we can also just
        # reload the emojis from disk
        with open(Path(__file__).parent / "emojis.json", encoding="utf8") as infile:
            self.emojis = ujson.loads(infile.read())

    def replace_emoji(self, name: str, to: str) -> None:
        if name not in self.emojis:
            raise InvalidEmoji
        self.emojis[name] = to


emoji_handler = EmojiHandler()  # initialize here so when it's changed other objects use this one


class SpotifyTrackPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.FullTrack], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None
        self.detailed = detailed

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, track: tekore.model.FullTrack) -> discord.Embed:
        self.current_track = track
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/track/{track.id}"
        artist_title = f"{track.name} by " + ", ".join(a.name for a in track.artists)
        album = getattr(track, "album", "")
        if album:
            album = f"[{album.name}](https://open.spotify.com/album/{album.id})"
        em.set_author(name=track.name[:256], url=url, icon_url=SPOTIFY_LOGO)
        em.description = f"[{artist_title}]({url})\n\n{album}"
        if track.album.images:
            em.set_thumbnail(url=track.album.images[0].url)
        if self.detailed:
            sp = tekore.Spotify(sender=menu.cog._sender)
            with sp.token_as(menu.user_token):
                details = await sp.track_audio_features(track.id)

            msg = await make_details(track, details)
            em.add_field(name="Details", value=box(msg[:1000], lang="css"))
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyArtistPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.FullArtist], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, artist: tekore.model.FullArtist) -> discord.Embed:
        self.current_track = artist
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/artist/{artist.id}"
        artist_title = f"{artist.name}"
        em.set_author(name=artist_title, url=url, icon_url=SPOTIFY_LOGO)
        sp = tekore.Spotify(sender=menu.cog._sender)
        with sp.token_as(menu.user_token):
            cur = await sp.artist_top_tracks(artist.id, "from_token")
        msg = "Top Tracks\n"
        for track in cur:
            msg += f"[{track.name}](https://open.spotify.com/track/{track.id})\n"
        em.description = msg
        if artist.images:
            em.set_thumbnail(url=artist.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyAlbumPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.FullAlbum], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, album: tekore.model.FullAlbum) -> discord.Embed:
        self.current_track = album
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/album/{album.id}"
        title = f"{album.name} by {humanize_list([a.name for a in album.artists])}"
        if len(title) > 256:
            title = f"{title[:253]}..."
        em.set_author(name=title, url=url, icon_url=SPOTIFY_LOGO)
        msg = "Tracks:\n"
        sp = tekore.Spotify(sender=menu.cog._sender)
        with sp.token_as(menu.user_token):
            cur = await sp.album(album.id)
        for track in cur.tracks.items:
            msg += f"[{track.name}](https://open.spotify.com/track/{track.id})\n"
        em.description = msg
        if album.images:
            em.set_thumbnail(url=album.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyPlaylistPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.SimplePlaylist], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, playlist: tekore.model.SimplePlaylist) -> discord.Embed:
        self.current_track = playlist
        em = None
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/playlist/{playlist.id}"
        artists = getattr(playlist, "artists", [])
        artist = humanize_list([a.name for a in artists])[:256]
        em.set_author(name=artist or playlist.name, url=url, icon_url=SPOTIFY_LOGO)
        user_spotify = tekore.Spotify(sender=menu.cog._sender)
        description = ""
        with user_spotify.token_as(menu.user_token):
            cur = await user_spotify.playlist_items(playlist.id)
            for track in cur.items[:10]:
                description += f"[{track.track.name}](https://open.spotify.com/track/{track.track.id})\n"

        em.description = description
        if playlist.images:
            em.set_thumbnail(url=playlist.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyNewPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.SimplePlaylist]) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, playlist: tekore.model.SimplePlaylist) -> discord.Embed:
        self.current_track = playlist
        em = None
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/playlist/{playlist.id}"
        artists = getattr(playlist, "artists", [])
        artist = humanize_list([a.name for a in artists])[:256]
        em.set_author(name=artist or playlist.name, url=url, icon_url=SPOTIFY_LOGO)
        user_spotify = tekore.Spotify(sender=menu.cog._sender)
        description = ""
        with user_spotify.token_as(menu.user_token):
            if playlist.type == "playlist":
                cur = await user_spotify.playlist_items(playlist.id)
                for track in cur.items[:10]:
                    description += f"[{track.track.name}](https://open.spotify.com/playlist/{track.track.id})\n"
            if playlist.type == "album":
                album = await user_spotify.album(playlist.id)
                cur = album.tracks
                for track in cur.items[:10]:
                    description += f"[{track.name}](https://open.spotify.com/album/{track.id})\n"

        em.description = description
        if playlist.images:
            em.set_thumbnail(url=playlist.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyEpisodePages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.FullEpisode], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None
        self.detailed = detailed

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, episode: tekore.model.FullEpisode) -> discord.Embed:
        self.current_track = episode
        show = episode.show
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/episode/{episode.id}"
        artist_title = f"{show.name} by {show.publisher}"
        em.set_author(name=artist_title[:256], url=url, icon_url=SPOTIFY_LOGO)
        em.description = f"[{episode.description[:1900]}]({url})\n"
        if episode.images:
            em.set_thumbnail(url=episode.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyShowPages(menus.ListPageSource):
    def __init__(self, items: list[tekore.model.FullShow], detailed: bool) -> None:
        super().__init__(items, per_page=1)
        self.current_track = None
        self.detailed = detailed

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, show: tekore.model.FullShow) -> discord.Embed:
        self.current_track = show
        em = discord.Embed(color=3092790)
        url = f"https://open.spotify.com/show/{show.id}"
        artist_title = f"{show.name} by {show.publisher}"
        em.set_author(name=artist_title[:256], url=url, icon_url=SPOTIFY_LOGO)
        em.description = f"[{show.description[:1900]}]({url})\n"
        if show.images:
            em.set_thumbnail(url=show.images[0].url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class SpotifyRecentSongPages(menus.ListPageSource):
    def __init__(self, tracks: list[tekore.model.PlayHistory], detailed: bool) -> None:
        super().__init__(tracks, per_page=1)
        self.current_track = None
        self.detailed = detailed

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, history: tekore.model.PlayHistory) -> discord.Embed:
        track = history.track
        self.current_track = track
        em = None
        em = discord.Embed(color=discord.Colour(0x1DB954), timestamp=history.played_at)
        url = f"https://open.spotify.com/track/{track.id}"
        artist_title = f"{track.name} by " + ", ".join(a.name for a in track.artists)
        em.set_author(name=track.name[:256], url=url, icon_url=SPOTIFY_LOGO)
        em.description = f"[{artist_title}]({url})\n"
        if track.album.images:
            em.set_thumbnail(url=track.album.images[0].url)
        if self.detailed:
            sp = tekore.Spotify(sender=menu.cog._sender)
            with sp.token_as(menu.user_token):
                details = await sp.track_audio_features(history.track.id)
            msg = await make_details(track, details)
            em.add_field(name="Details", value=box(msg[:1000], lang="css"))
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()} | Played at")
        return em


class SpotifyPlaylistsPages(menus.ListPageSource):
    def __init__(self, playlists: list[tekore.model.SimplePlaylist]) -> None:
        super().__init__(playlists, per_page=10)

    async def format_page(self, menu: menus.MenuPages, playlists: list[tekore.model.SimplePlaylist]) -> discord.Embed:
        em = None
        em = discord.Embed(color=3092790)
        em.set_author(name=f"{menu.ctx.author.display_name}'s Spotify Playlists", icon_url=menu.ctx.author.avatar_url)
        msg = "".join(
            f"[{playlist.name}](https://open.spotify.com/playlist/{playlist.id})\n" if playlist.public else f"{playlist.name}\n" for playlist in playlists
        )

        em.description = msg
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}", icon_url=SPOTIFY_LOGO)
        return em


class SpotifyTopTracksPages(menus.ListPageSource):
    def __init__(self, playlists: list[tekore.model.FullTrack]) -> None:
        super().__init__(playlists, per_page=10)

    async def format_page(self, menu: menus.MenuPages, tracks: list[tekore.model.FullTrack]) -> discord.Embed:
        em = None
        em = discord.Embed(color=3092790)
        em.set_author(name=f"{menu.ctx.author.display_name}'s Top Tracks", icon_url=menu.ctx.author.avatar_url)
        msg = ""
        for track in tracks:
            artist = humanize_list([a.name for a in track.artists])
            msg += f"[{track.name} by {artist}](https://open.spotify.com/track/{track.id})\n"
        em.description = msg
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}", icon_url=SPOTIFY_LOGO)
        return em


class SpotifyTopArtistsPages(menus.ListPageSource):
    def __init__(self, playlists: list[tekore.model.FullArtist]) -> None:
        super().__init__(playlists, per_page=10)

    async def format_page(self, menu: menus.MenuPages, artists: list[tekore.model.FullArtist]) -> discord.Embed:
        em = None
        em = discord.Embed(color=3092790)
        em.set_author(name=f"{menu.ctx.author.display_name}'s Top Artists", icon_url=menu.ctx.author.avatar_url)
        msg = "".join(f"[{artist.name}](https://open.spotify.com/artist/{artist.id})\n" for artist in artists)

        em.description = msg
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}", icon_url=SPOTIFY_LOGO)
        return em


class SpotifyPages(menus.PageSource):
    def __init__(self, user_token: tekore.Token, sender: tekore.AsyncSender, detailed: bool, redis) -> None:
        super().__init__()
        self.user_token = user_token
        self.redis = redis
        self.sender = sender
        self.detailed = detailed
        self.current_track = None

    async def format_page(self, menu: menus.MenuPages, cur_state: tuple[tekore.model.CurrentlyPlayingContext, bool]) -> discord.Embed:
        state = cur_state[0]
        is_liked = cur_state[1]
        self.current_track = state.item
        if getattr(state.item, "is_local", False):
            url = "https://open.spotify.com/"
            artist_title = f"{state.item.name} by " + ", ".join(a.name for a in state.item.artists)
            image = SPOTIFY_LOGO
        elif state.item.type == "episode":
            url = f"https://open.spotify.com/episode/{state.item.id}"
            artist_title = state.item.name
            image = state.item.images[0].url
        else:
            url = f"https://open.spotify.com/track/{state.item.id}"
            artist_title = f"{state.item.name} by " + ", ".join(a.name for a in state.item.artists)
            image = state.item.album.images[0].url
        album = getattr(state.item, "album", "")
        if album:
            album = f"[{album.name}](https://open.spotify.com/album/{album.id})"
        em = discord.Embed()

        lookup = await get_image_colors2(image)
        if lookup:
            em.color = discord.Color(lookup.dominant.decimal)
        em.set_author(name=f"{menu.ctx.author.display_name} is currently listening to", icon_url=menu.ctx.author.avatar_url, url=url)
        repeat = f"{REPEAT_STATES[state.repeat_state]} |" if state.repeat_state != "off" else ""
        shuffle = "\N{TWISTED RIGHTWARDS ARROWS} |" if state.shuffle_state else ""
        liked = "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}" if is_liked else ""
        footer = f"{repeat}{shuffle}{liked}"
        footer = footer or "melanie"
        em.set_footer(text=footer, icon_url=footer_gif)
        em.description = f"[{artist_title}]({url})\n\n{album}"
        with contextlib.suppress(tekore.NotFound):
            if self.detailed and not getattr(state.item, "is_local", False):
                sp = tekore.Spotify(sender=self.sender)
                with sp.token_as(self.user_token):
                    details = await sp.track_audio_features(state.item.id)
                msg = await make_details(state.item, details)
                em.add_field(name="Details", value=box(msg[:1000], lang="css"))
        em.set_thumbnail(url=image)
        return em

    def is_paginating(self) -> bool:
        """An abstract method that notifies the :class:`MenuPages` whether or not
        to start paginating. This signals whether to add reactions or not.
        Subclasses must implement this.

        Returns
        -------
        :class:`bool`
            Whether to trigger pagination.

        """
        return True

    def get_max_pages(self) -> None:
        """An optional abstract method that retrieves the maximum number of pages
        this page source has. Useful for UX purposes.
        The default implementation returns ``None``.

        Returns
        -------
        Optional[:class:`int`]
            The maximum number of pages required to properly
            paginate the elements, if given.
        """
        return None

    @alru_cache(ttl=2.2)
    async def get_current_track(self) -> tuple[Optional[Track], Optional[CurrentlyPlayingContext]]:
        user_spotify = tekore.Spotify(sender=self.sender)
        with user_spotify.token_as(self.user_token):
            cur_state: CurrentlyPlayingContext = await user_spotify.playback()
            return (cur_state.item, cur_state) if cur_state else (None, None)

    async def get_page(self, page_number):
        """|coro| An abstract method that retrieves an object representing the
        object to format. Subclasses must implement this.

        .. note::
            The page_number is zero-indexed between [0, :meth:`get_max_pages`),
            if there is a maximum number of pages.

        Parameters
        ----------
        page_number: :class:`int`
            The page number to access.

        Returns
        -------
        Any
            The object represented by that page.
            This is passed into :meth:`format_page`.

        """
        try:
            _, cur_state = await self.get_current_track()
            if not cur_state:
                raise NotPlaying
            is_liked = False
            if not getattr(cur_state.item, "is_local", False):
                song = cur_state.item.id
                user_spotify = tekore.Spotify(sender=self.sender)
                with user_spotify.token_as(self.user_token):
                    liked = await user_spotify.saved_tracks_contains([song])
                    is_liked = liked[0]
        except tekore.Unauthorised:
            raise
        return cur_state, is_liked


class SpotifyUserMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        cog: SpotifyCog,
        user_token: tekore.Token,
        use_external: bool,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 300,
        auto_refresh_for: int | None = None,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            source,
            clear_reactions_after=clear_reactions_after,
            delete_message_after=delete_message_after,
            timeout=timeout,
            message=message,
            **kwargs,
        )
        self._source.cog = cog
        self.user_token = user_token
        self.cog = cog

        self.use_external = use_external
        self.add_button(menus.Button(emoji_handler.get_emoji("like", self.use_external), self.like_song, position=menus.First(0)))
        self.add_button(menus.Button(emoji_handler.get_emoji("next", self.use_external), self.skip_next, position=menus.First(1)))
        self.auto_refresh_for: int = auto_refresh_for
        if auto_refresh_for:
            if str(user_token) in self.cog.user_tasks:
                self.cog.user_tasks[str(user_token)].cancel()
            self.refresh_task = self.cog.user_tasks[str(user_token)] = spawn_task(self.run_refresh_loop(auto_refresh_for), self.cog.active_tasks)

        else:
            self.refresh_task = self.cog.user_tasks[str(user_token)] = spawn_task(self.run_color_update(), self.cog.active_tasks)

    async def get_track_colors(self, state: CurrentlyPlayingContext):
        image = None
        if getattr(state.item, "is_local", False):
            image = SPOTIFY_LOGO
        elif state.item.type == "episode":
            image = state.item.images[0].url
        else:
            image = state.item.album.images[0].url
        if album := getattr(state.item, "album", ""):
            album = f"[{album.name}](https://open.spotify.com/album/{album.id})"
        if image:
            return await get_image_colors2(image)

    async def run_color_update(self):
        max_runtime = time.time() + 300
        _track, state = await self._source.get_current_track()
        current = _track.id
        while time.time() < max_runtime:
            await asyncio.sleep(2)
            _track, state = await self._source.get_current_track()
            if not _track:
                continue
            if _track.id != current:
                await self.get_track_colors(state)
                current = _track.id

    async def run_refresh_loop(self, duration):
        max_runtime = time.time() + duration
        redis = get_redis()

        await asyncio.sleep(0.2)
        _track, state = await self._source.get_current_track()
        if not _track:
            return

        current = _track.id
        while time.time() < max_runtime:
            await asyncio.sleep(random.uniform(0.8, 1))
            _track, state = await self._source.get_current_track()
            if not _track:
                continue
            if _track.id != current:
                self._source = SpotifyPages(user_token=self.user_token, redis=redis, sender=self.cog._sender, detailed=False)
                self._source.cog = self.cog
                await self.show_page(0)
                current = _track.id
                # show_page(self, page_number) -> N

    async def finalize(self, timed_out: bool) -> None:
        del self.cog.user_menus[self.ctx.author.id]

    async def _internal_loop(self):
        try:
            self.__timed_out = False
            loop = self.bot.loop
            # Ensure the name exists for the cancellation handling
            tasks = []
            while self._running:
                tasks = [
                    asyncio.ensure_future(self.bot.wait_for("raw_reaction_add", check=self.reaction_check)),
                    asyncio.ensure_future(self.bot.wait_for("raw_reaction_remove", check=self.reaction_check)),
                ]
                done, pending = await asyncio.wait(tasks, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()

                if len(done) == 0:
                    raise asyncio.TimeoutError

                # Exception will propagate if e.g. cancelled or timed out
                payload = done.pop().result()
                loop.create_task(self.update(payload))

                # NOTE: Removing the reaction ourselves after it's been done when
                # mixed with the checks above is incredibly racy.
                # There is no guarantee when the MESSAGE_REACTION_REMOVE event will
                # be called, and chances are when it does happen it'll always be
                # after the remove_reaction HTTP call has returned back to the caller
                # which means that the stuff above will catch the reaction that we
                # just removed.

                # For the future sake of myself and to save myself the hours in the future
                # consider this my warning.

        except TimeoutError:
            self.__timed_out = True
        finally:
            self._event.set()

            # Cancel any outstanding tasks (if any)
            for task in tasks:
                task.cancel()

            try:
                await self.finalize(self.__timed_out)
            except Exception:
                pass
            finally:
                self.__timed_out = False

            # Can't do any requests if the bot is closed
            if self.bot.is_closed():
                return

            # Wrap it in another block anyway just to ensure
            # nothing leaks out during clean-up
            with contextlib.suppress(Exception):
                if self.delete_message_after:
                    return await self.message.delete()

                if self.clear_reactions_after:
                    if self._can_remove_reactions:
                        return await self.message.clear_reactions()

                    for button_emoji in self.buttons:
                        try:
                            await self.message.remove_reaction(button_emoji, self.__me)
                        except discord.HTTPException:
                            continue

    async def update(self, payload) -> None:
        """|coro|.

        Updates the menu after an event has been received.

        Parameters
        ----------
        payload: :class:`discord.RawReactionActionEvent`
            The reaction event that triggered this update.

        """
        if not payload:
            return
        button = self.buttons[payload.emoji]
        if not self._running:
            return

        try:
            if button.lock:
                async with self._lock:
                    if self._running:
                        await button(self, payload)
            else:
                await button(self, payload)
        except Exception:
            log.opt(exception=True).debug("Ignored exception on reaction event")

    async def send_initial_message(self, ctx, channel):
        """|coro| The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.

        """
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        redis = get_redis()
        msg = await channel.send(**kwargs)
        with log.catch(exclude=asyncio.CancelledError):
            await redis.set(f"emitted_msg_stub:{xxhash.xxh32_hexdigest(str(msg.id))}", str(time.time()), ex=220)
        self.cog.current_menus[msg.id] = ctx.author.id
        self.cog.user_menus[ctx.author.id] = msg.jump_url
        return msg

    async def show_page(self, page_number) -> None:
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)

        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            self.stop()

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        with contextlib.suppress(IndexError):
            if max_pages is None or page_number < max_pages and page_number >= 0:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif page_number >= max_pages:
                await self.show_page(0)
            else:
                await self.show_page(max_pages - 1)

    def reaction_check(self, payload) -> bool:
        """Just extends the default reaction_check to use owner_ids."""
        if payload.message_id != self.message.id:
            return False
        if payload.user_id != self._author_id:
            return False
        return payload.emoji in self.buttons

    def _is_other_user(self) -> bool:
        return isinstance(self.source, SpotifyTrackPages)

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages <= 2

    async def play_pause(self, payload) -> None:
        """Go to the previous page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                    return
                if cur.item.id == self.source.current_track.id:
                    if cur.is_playing:
                        await user_spotify.playback_pause()
                    else:
                        await user_spotify.playback_resume()
                elif self.source.current_track.type == "track":
                    await user_spotify.playback_start_tracks([self.source.current_track.id])
                else:
                    await user_spotify.playback_start_context(self.source.current_track.uri)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await asyncio.sleep(1)
        await self.show_checked_page(0)

    async def repeat(self, payload) -> None:
        """Go to the next page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if cur.repeat_state == "context":
                    state = "track"
                elif cur.repeat_state == "off":
                    state = "context"
                elif cur.repeat_state == "track":
                    state = "off"
                await user_spotify.playback_repeat(state)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await asyncio.sleep(1)
        await self.show_checked_page(0)

    async def shuffle(self, payload) -> None:
        """Go to the next page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                state = not cur.shuffle_state
                await user_spotify.playback_shuffle(state)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await asyncio.sleep(1)
        await self.show_checked_page(0)

    async def like_song(self, payload) -> None:
        """Go to the next page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                await user_spotify.saved_tracks_add([self.source.current_track.id])
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await self.show_checked_page(0)

    async def skip_previous(self, payload) -> None:
        """Go to the first page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                await user_spotify.playback_previous()
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await asyncio.sleep(1)
        await self.show_page(0)

    async def skip_next(self, payload) -> None:
        """Go to the last page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                await user_spotify.playback_next()
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        if isinstance(self.source, SpotifyTrackPages):
            self._source = SpotifyPages(user_token=self.user_token, sender=self.cog._sender, detailed=self.source.detailed)
            self._source.cog = self.cog
        await asyncio.sleep(1)
        await self.show_page(0)

    # @menus.button("\N{CROSS MARK}")
    # async def stop_pages(self, payload: discord.RawReactionActionEvent) -> None:
    #     """stops the pagination session."""
    #     if self.message.id in self.cog.current_menus:
    #     if self.ctx.author.id in self.cog.user_menus:


class SpotifySearchMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        cog: commands.Cog,
        user_token: tekore.Token,
        use_external: bool,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 60,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            source,
            clear_reactions_after=clear_reactions_after,
            delete_message_after=delete_message_after,
            timeout=timeout,
            message=message,
            **kwargs,
        )
        self.user_token = user_token
        self.use_external = use_external
        self.cog = cog
        self.add_button(menus.Button(emoji_handler.get_emoji("next", self.use_external), self.skip_next, position=menus.First(7)))
        self.add_button(menus.Button(emoji_handler.get_emoji("previous", self.use_external), self.skip_previous, position=menus.First(0)))
        self.add_button(menus.Button(emoji_handler.get_emoji("playpause", self.use_external), self.play_pause, position=menus.First(2)))
        self.add_button(
            menus.Button(emoji_handler.get_emoji("playall", self.use_external), self.play_pause_all, position=menus.First(3), skip_if=self._skip_play_all),
        )
        self.add_button(
            menus.Button(emoji_handler.get_emoji("queue", self.use_external), self.queue_song_next, position=menus.First(4), skip_if=self._skip_queue_next),
        )
        self.add_button(menus.Button(emoji_handler.get_emoji("like", self.use_external), self.like_song, position=menus.First(5)))
        self.add_button(menus.Button(emoji_handler.get_emoji("back_left", self.use_external), self.go_to_previous_page, position=menus.First(1)))
        self.add_button(menus.Button(emoji_handler.get_emoji("play", self.use_external), self.go_to_next_page, position=menus.First(6)))

    async def update(self, payload) -> None:
        """|coro|.

        Updates the menu after an event has been received.

        Parameters
        ----------
        payload: :class:`discord.RawReactionActionEvent`
            The reaction event that triggered this update.

        """
        button = self.buttons[payload.emoji]
        if not self._running:
            return

        try:
            if button.lock:
                async with self._lock:
                    if self._running:
                        await button(self, payload)
            else:
                await button(self, payload)
        except Exception:
            log.opt(exception=True).debug("Ignored exception on reaction event")

    async def send_initial_message(self, ctx, channel):
        """|coro| The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.

        """
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        msg = await channel.send(**kwargs)
        redis = get_redis()
        with log.catch(exclude=asyncio.CancelledError):
            await redis.set(f"emitted_msg_stub:{xxhash.xxh32_hexdigest(str(msg.id))}", str(time.time()), ex=220)
        self.cog.current_menus[msg.id] = ctx.author.id
        return msg

    async def show_page(self, page_number) -> None:
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            self.stop()

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        with contextlib.suppress(IndexError):
            if max_pages is None or page_number < max_pages and page_number >= 0:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif page_number >= max_pages:
                await self.show_page(0)
            else:
                await self.show_page(max_pages - 1)

    def reaction_check(self, payload) -> bool:
        """Just extends the default reaction_check to use owner_ids."""
        if payload.message_id != self.message.id:
            return False
        if payload.user_id != self._author_id:
            return False
        return payload.emoji in self.buttons

    def _skip_single_arrows(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages == 1

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages <= 2

    def _skip_play_all(self) -> bool:
        return not isinstance(self._source.entries[0], tekore.model.FullTrack)

    def _skip_queue_next(self) -> bool:
        return not isinstance(self._source.current_track, tekore.model.FullTrack)

    async def go_to_previous_page(self, payload) -> None:
        """Go to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    async def go_to_next_page(self, payload) -> None:
        """Go to the next page."""
        await self.show_checked_page(self.current_page + 1)

    async def play_pause(self, payload) -> None:
        """Go to the previous page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                    return
                if cur.item.id == self.source.current_track.id:
                    if cur.is_playing:
                        await user_spotify.playback_pause()
                    else:
                        await user_spotify.playback_resume()
                elif self.source.current_track.type == "track":
                    await user_spotify.playback_start_tracks([self.source.current_track.id])
                else:
                    await user_spotify.playback_start_context(self.source.current_track.uri)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")

    async def play_pause_all(self, payload) -> None:
        """Go to the previous page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                    return
                else:
                    if self.source.current_track.type == "track":
                        await user_spotify.playback_start_tracks([i.id for i in self.source.entries])
                    else:
                        await user_spotify.playback_start_context(self.source.current_track.uri)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")

    async def queue_song_next(self, payload) -> None:
        """Go to the previous page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await self.ctx.send("I could not find an active device to play songs on.")
                    return
                else:
                    if self.source.current_track.type == "track":
                        await user_spotify.playback_queue_add(self.source.current_track.uri)
                        await self.ctx.send(f"{self.source.current_track.name} has been added to your queue.")
                    else:
                        await user_spotify.playback_start_context(self.source.current_track.uri)
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")

    async def like_song(self, payload) -> None:
        """Go to the next page."""
        try:
            user_spotify = tekore.Spotify(sender=self.cog._sender)
            with user_spotify.token_as(self.user_token):
                await user_spotify.saved_tracks_add([self.source.current_track.id])
        except tekore.Unauthorised:
            await self.ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await self.ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                await self.ctx.send("This action is prohibited for non-premium users.")
            else:
                await self.ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await self.ctx.send("Unknown error. This has been reported and should be resolved soon.")
        await self.show_checked_page(0)

    async def skip_previous(self, payload) -> None:
        """Go to the first page."""
        await self.show_page(0)

    async def skip_next(self, payload) -> None:
        """Go to the last page."""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(self._source.get_max_pages() - 1)

    @menus.button("\N{CROSS MARK}")
    async def stop_pages(self, payload: discord.RawReactionActionEvent) -> None:
        """Stops the pagination session."""
        self.stop()
        del self.cog.current_menus[self.message.id]
        await self.message.delete()


class SpotifyBaseMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        cog: commands.Cog,
        user_token: tekore.Token,
        use_external: bool,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 60,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            source,
            clear_reactions_after=clear_reactions_after,
            delete_message_after=delete_message_after,
            timeout=timeout,
            message=message,
            **kwargs,
        )
        self.user_token = user_token
        self.cog = cog

    async def update(self, payload) -> None:
        """|coro|.

        Updates the menu after an event has been received.

        Parameters
        ----------
        payload: :class:`discord.RawReactionActionEvent`
            The reaction event that triggered this update.

        """
        button = self.buttons[payload.emoji]
        if not self._running:
            return

        try:
            if button.lock:
                async with self._lock:
                    if self._running:
                        await button(self, payload)
            else:
                await button(self, payload)
        except Exception:
            log.opt(exception=True).debug("Ignored exception on reaction event")

    async def send_initial_message(self, ctx, channel):
        """|coro| The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.

        """
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        msg = await channel.send(**kwargs)
        self.cog.current_menus[msg.id] = ctx.author.id
        return msg

    async def show_page(self, page_number) -> None:
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        await self.message.edit(**kwargs)

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        with contextlib.suppress(IndexError):
            if max_pages is None or page_number < max_pages and page_number >= 0:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif page_number >= max_pages:
                await self.show_page(0)
            else:
                await self.show_page(max_pages - 1)

    def reaction_check(self, payload) -> bool:
        """Just extends the default reaction_check to use owner_ids."""
        if payload.message_id != self.message.id:
            return False
        if payload.user_id not in (*self.bot.owner_ids, self._author_id):
            return False
        return payload.emoji in self.buttons

    def _skip_single_arrows(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages == 1

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages <= 2

    @menus.button("\N{BLACK LEFT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}", position=menus.First(1))
    async def go_to_previous_page(self, payload) -> None:
        """Go to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    @menus.button("\N{BLACK RIGHT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}", position=menus.Last(0))
    async def go_to_next_page(self, payload) -> None:
        """Go to the next page."""
        await self.show_checked_page(self.current_page + 1)

    @menus.button(
        "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}",
        position=menus.First(0),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_first_page(self, payload) -> None:
        """Go to the first page."""
        await self.show_page(0)

    @menus.button(
        "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}",
        position=menus.Last(1),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_last_page(self, payload) -> None:
        """Go to the last page."""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(self._source.get_max_pages() - 1)

    @menus.button("\N{CROSS MARK}")
    async def stop_pages(self, payload: discord.RawReactionActionEvent) -> None:
        """Stops the pagination session."""
        self.stop()
        del self.cog.current_menus[self.message.id]
        await self.message.delete()
