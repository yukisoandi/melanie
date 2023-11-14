from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping
from copy import copy
from typing import Any, Optional, Union

import discord
import regex as re
import shortuuid
import tekore
import tekore.model
from aiomisc.utils import cancel_tasks
from loguru import logger as log
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list
from melaniebot.core.utils.menus import start_adding_reactions
from melaniebot.core.utils.predicates import ReactionPredicate
from tekore import ConversionError, from_url
from tekore.model import FullTrackPaging

from melanie import (
    BaseModel,
    aiter,
    default_lock_cache,
    footer_gif,
    get_redis,
    make_e,
    spawn_task,
)

from .helpers import (
    SPOTIFY_RE,
    InvalidEmoji,
    NotPlaying,
    ScopeConverter,
    SearchTypes,
    SpotifyURIConverter,
    time_convert,
)
from .menus import (
    SpotifyAlbumPages,
    SpotifyArtistPages,
    SpotifyBaseMenu,
    SpotifyEpisodePages,
    SpotifyNewPages,
    SpotifyPages,
    SpotifyPlaylistPages,
    SpotifyPlaylistsPages,
    SpotifyRecentSongPages,
    SpotifySearchMenu,
    SpotifyShowPages,
    SpotifyTopArtistsPages,
    SpotifyTopTracksPages,
    SpotifyTrackPages,
    SpotifyUserMenu,
    emoji_handler,
)
from .models import SpotifyStateHolder, SpotifyStateInfo
from .sender import CurlSender

ActionConverter = commands.get_dict_converter(*emoji_handler.emojis.keys(), delims=[" ", ",", ";"])


def _(x):
    return x


DASHBOARD = False


class SpToken(BaseModel):
    uses_pkce: Optional[bool] = False
    expires_in: Optional[int] = None
    scope: Optional[str] = None
    token_type: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[int] = None
    is_expiring: Optional[bool]

    def __init__(self, *a, **ka) -> None:
        super().__init__(*a, **ka)
        self.expires_in = self.expires_at - int(time.time()) if self.expires_at else None
        self.is_expiring = self.expires_at < 60 if self.expires_at else None


class UserSettins(BaseModel):
    token: Union[SpToken, Any] = {}
    listen_for: dict[str, str] = {}
    refresh_failures: int = 0


class Spotify(commands.Cog):
    """Display information from Spotify's API."""

    __version__ = "1.6.1"

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot
        self.locks = default_lock_cache()
        self.tasks: dict[int, asyncio.Task] = {}
        self.config = Config.get_conf(self, identifier=218773382617890828)
        self.config.register_user(**SpToken().dict(), show_private=False)
        self.config.register_guild(clear_reactions_after=True, delete_message_after=False, menu_timeout=220)
        self.config.register_global(
            emojis={},
            scopes=[
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
            ],
            version="0.0.0",
        )

        self._app_token = None
        self._tokens: tuple[str] = None
        self._spotify_client = None
        self._sender = None
        self._credentials = None
        self._ready = asyncio.Event()
        self.HAS_TOKENS = False
        self.current_menus = {}
        self.user_menus = {}
        self.GENRES = []
        self.debug = False
        self.dashboard_authed = []
        self.temp_cache = {}
        self.active_tasks = []
        self._sender = CurlSender(self.bot.htx)
        spawn_task(self.initialize(), self.active_tasks)
        self.user_tasks: dict[int, asyncio.Task] = {}

    async def migrate_settings(self) -> None:
        if await self.config.version() >= "1.4.9":
            return
        all_users = await self.config.all_users()
        for user_id, data in all_users.items():
            if not data["listen_for"]:
                continue
            new_data = {} if isinstance(data["listen_for"], list) else {v: k for k, v in data["listen_for"].items()}
            await self.config.user_from_id(user_id).listen_for.set(new_data)
        await self.config.version.set(self.__version__)

    async def initialize(self) -> None:
        await self.bot.waits_uptime_for(10)
        await self.migrate_settings()
        tokens = await self.bot.get_shared_api_tokens("spotify")
        if not tokens:
            self._ready.set()
            return
        self._tokens = (tokens.get("client_id"), tokens.get("client_secret"), tokens.get("redirect_uri", "https://localhost/"))
        self._credentials = tekore.Credentials(*self._tokens, sender=self._sender)
        self._app_token = tekore.request_client_token(*self._tokens[:2])
        self._spotify_client = tekore.Spotify(self._app_token, sender=self._sender)
        self.GENRES = await self._spotify_client.recommendation_genre_seeds()
        emojis = await self.config.emojis()
        for name, emoji in emojis.items():
            with contextlib.suppress(InvalidEmoji):
                emoji_handler.replace_emoji(name, emoji)
        self._ready.set()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        await self._ready.wait()

    def cog_unload(self) -> None:
        cancel_tasks(self.active_tasks)

    async def get_user_auth(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Handles getting and saving user authorization information."""
        author = user or ctx.author
        if not self._credentials:
            await ctx.send(
                f"The bot owner needs to set their Spotify credentials before this command can be used. See `{ctx.clean_prefix}spotify set creds` for more details.",
            )
            return
        user_tokens = await self.config.user(author).token()
        if user_tokens:
            user_tokens["expires_in"] = user_tokens.get("expires_at", 1622167242) - int(time.time())
            user_token = tekore.Token(user_tokens, user_tokens["uses_pkce"])
            if user_token.is_expiring:
                try:
                    user_token = await self._credentials.refresh(user_token)
                except tekore.BadRequest:
                    await ctx.send("Your refresh token has been revoked, clearing data.")
                    await self.config.user(author).token.clear()
                    return
                await self.save_token(author, user_token)
            return user_token
        if author.id in self.temp_cache:
            await ctx.send("I've already sent you a link for authorization, please complete that first before trying a new command.")
            return
        try:
            return await self.ask_for_auth(ctx, author)
        except discord.errors.Forbidden:
            await ctx.send("You have blocked direct messages, please enable them to authorize spotify commands.")

    @staticmethod
    def get_url() -> tuple[str, str]:
        state = shortuuid.random(8)

        return (
            state,
            f"https://discord.com/api/oauth2/authorize?client_id=928394879200034856&redirect_uri=https://dev.melaniebot.net/spotify_exchange&response_type=code&scope=identify&state={state}",
        )

    async def ask_for_auth(self, ctx: commands.Context, author: discord.User):
        state, url = self.get_url()
        redis = get_redis()
        info = SpotifyStateInfo.generate_new(state=state, user=author, channel_id=ctx.channel.id, guild_id=ctx.guild.id)
        await redis.set(info.init_key, info.json(), ex=90)

        async def wait_for_login() -> tekore.Token:
            while True:
                await asyncio.sleep(0.2)
                data = await redis.get(info.exchange_key)
                if data:
                    data = SpotifyStateHolder.parse_raw(data)
                    await self.config.user(author).token.set({})
                    async with self.config.user(author).token() as token:
                        token.update(data.token.dict())
                        token["expires_in"] = token["expires_at"] - int(time.time())
                        user_token = tekore.Token(token, token["uses_pkce"])
                    return user_token

        embed = discord.Embed()
        embed.title = "Grant me access to control your Spotify 🥺"

        embed.description = f"Follow this link: {url}\n\n **NOTE**: If you're a mobile user, clicking on the link directly will no longer work. Press and hold the link, copy, and **open it in Safari browser.** Desktop users can click the link normally. "
        embed.color = discord.Color(0x1DB954)

        embed.set_footer(icon_url=footer_gif, text="melanie ^_^")

        msg = await ctx.send(embed=embed)

        try:
            async with asyncio.timeout(200):
                login_task = spawn_task(wait_for_login(), self.active_tasks)
                token = await login_task
                await msg.delete(delay=0.1)
                await ctx.send(embed=make_e("Your Spotify is connected!", tip="browse ;help sp to see all commands"))
                return token
        except TimeoutError:
            return None

    async def save_token(self, author: discord.User, user_token: tekore.Token) -> None:
        async with self.config.user(author).token() as token:
            token["access_token"] = user_token.access_token
            token["refresh_token"] = user_token.refresh_token
            token["expires_at"] = user_token.expires_at
            token["scope"] = str(user_token.scope)
            token["uses_pkce"] = user_token.uses_pkce
            token["token_type"] = user_token.token_type

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handles listening for reactions and parsing."""
        if payload.message_id in self.current_menus and self.current_menus[payload.message_id] == payload.user_id:
            log.debug("Menu reaction from the same user ignoring")
            return
        listen_for = await self.config.user_from_id(payload.user_id).listen_for()
        if not listen_for:
            return
        token = await self.config.user_from_id(payload.user_id).token()
        if not token:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        message = await aiter(self.bot.cached_messages).find(lambda x: x.id == payload.message_id, None)
        if not message:
            return
        content = message.content
        if message.embeds:
            em_dict = message.embeds[0].to_dict()
            content += " ".join(v for k, v in em_dict.items() if k in ["title", "description"])
            if "title" in em_dict and "url" in em_dict["title"]:
                content += " " + em_dict["title"]["url"]
            if "fields" in em_dict:
                for field in em_dict["fields"]:
                    content += " " + field["name"] + " " + field["value"]

            if "url" in em_dict:
                content += em_dict["url"]
            log.debug(content)
        content = content.replace("🧑‍🎨", ":artist:")
        # because discord will replace this in URI's automatically 🙄
        song_data = SPOTIFY_RE.finditer(content)
        tracks = []
        albums = []
        playlists = []
        if song_data:
            new_uri = ""
            for match in song_data:
                new_uri = f"spotify:{match.group(2)}:{match.group(3)}"
                if match.group(2) == "track":
                    tracks.append(match.group(3))
                if match.group(2) == "album":
                    albums.append(match.group(3))
                if match.group(2) == "playlist":
                    playlists.append(match.group(3))
        ctx = await self.bot.get_context(message)
        user = self.bot.get_user(payload.user_id)
        if not user:
            return
        if str(payload.emoji) not in listen_for:
            return

        user_token = await self.get_user_auth(ctx, user)
        if not user_token:
            return
        user_spotify = tekore.Spotify(sender=self._sender)
        action = listen_for[str(payload.emoji)]
        if action in ["play", "playpause"]:
            # play the song if it exists
            try:
                with user_spotify.token_as(user_token):
                    if tracks:
                        await user_spotify.playback_start_tracks(tracks)
                        await ctx.react_quietly(payload.emoji)
                        return
                    elif new_uri:
                        await user_spotify.playback_start_context(new_uri)
                        await ctx.react_quietly(payload.emoji)
                        return
                    elif message.embeds:
                        em = message.embeds[0]
                        query = None
                        if em.description:
                            look = f"{em.title or ''}-{em.description}"
                            if find := re.search(r"\[(.+)\]", look):
                                query = find.group(1)
                        else:
                            query = em.title or ""
                        if not query or query == "-":
                            return
                        search = await user_spotify.search(query, ("track",), "from_token", limit=50)
                        tracks = search[0].items
                        if tracks:
                            await user_spotify.playback_start_tracks([t.id for t in tracks])
                            await ctx.react_quietly(payload.emoji)
            except Exception:
                log.exception("Error on reaction add play")
        if action == "like":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    if tracks:
                        await user_spotify.saved_tracks_add(tracks)
                    if albums:
                        await user_spotify.saved_albums_add(albums)
                    if playlists:
                        for playlist in playlists:
                            await user_spotify.playlists_add(playlist)
                await ctx.react_quietly(payload.emoji)
        elif action == "next":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    await user_spotify.playback_next()
                await ctx.react_quietly(payload.emoji)
        elif action == "pause":
            with contextlib.suppress(Exception), user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                if cur.is_playing:
                    await user_spotify.playback_pause()
                await ctx.react_quietly(payload.emoji)
        elif action == "previous":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    await user_spotify.playback_previous()
                await ctx.react_quietly(payload.emoji)
        elif action == "queue":
            # append a track to the queue
            try:
                with user_spotify.token_as(user_token):
                    if tracks:
                        for track in tracks:
                            await user_spotify.playback_queue_add(f"spotify:track:{track}")
                        await ctx.react_quietly(payload.emoji)
                        return
                    elif message.embeds:
                        em = message.embeds[0]
                        query = None
                        if em.description:
                            look = f"{em.title or ''}-{em.description}"
                            if find := re.search(r"\[(.+)\]", look):
                                query = find.group(1)
                        else:
                            query = em.title or ""
                        if not query or query == "-":
                            return
                        search = await user_spotify.search(query, ("track",), "from_token", limit=50)
                        if tracks := search[0].items:
                            await user_spotify.playback_start_tracks([t.id for t in tracks])
                            await ctx.react_quietly(payload.emoji)
            except Exception:
                log.exception("Error on reaction add play")
        elif action == "repeat":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.playback()
                    if cur.repeat_state == "context":
                        state = "off"
                    elif cur.repeat_state == "off":
                        state = "context"
                    await user_spotify.playback_repeat(state)
                await ctx.react_quietly(payload.emoji)
        elif action == "repeatone":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.playback()
                    if cur.repeat_state == "off":
                        state = "track"
                    elif cur.repeat_state == "track":
                        state = "off"
                    await user_spotify.playback_repeat(state)
                await ctx.react_quietly(payload.emoji)
        elif action == "shuffle":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(self.user_token):
                    cur = await user_spotify.playback()
                    if not cur:
                        return
                    await user_spotify.playback_shuffle(not cur.shuffle_state)
                await ctx.react_quietly(payload.emoji)
        elif action == "volume_down":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.playback()
                    volume = cur.device.volume_percent - 10
                    await user_spotify.playback_volume(volume)
                await ctx.react_quietly(payload.emoji)
        elif action == "volume_mute":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.playback()
                    await user_spotify.playback_volume(0)
                await ctx.react_quietly(payload.emoji)
        elif action == "volume_up":
            with contextlib.suppress(Exception):
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.playback()
                    volume = cur.device.volume_percent + 10
                    await user_spotify.playback_volume(volume)
                await ctx.react_quietly(payload.emoji)

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Mapping[str, str]) -> None:
        if service_name == "spotify":
            await self.initialize()

    @commands.group(name="spotify", invoke_without_command=True, aliases=["sp"])
    async def spotify_com(self, ctx: commands.Context, *, play: str = None) -> None:
        """Spotify commands."""
        return await ctx.invoke(self.bot.get_command("spotify play"), url_or_playlist_name=play) if play else await ctx.send_help()

    @spotify_com.group(name="set")
    async def spotify_set(self, ctx: commands.Context) -> None:
        """Setup Spotify cog."""

    @spotify_com.group(name="playlist", aliases=["playlists"])
    async def spotify_playlist(self, ctx: commands.Context) -> None:
        """View Spotify Playlists."""

    @spotify_com.group(name="artist", aliases=["artists"])
    async def spotify_artist(self, ctx: commands.Context) -> None:
        """View Spotify Artist info."""

    @spotify_set.command(name="listen")
    async def set_reaction_listen(self, ctx: commands.Context, *, listen_for: ActionConverter) -> None:
        """Set the bot to listen for specific emoji reactions on messages.

        If the message being reacted to has somthing valid to search
        for the bot will attempt to play the found search on spotify for you.

        `<listen_for>` Must be one of the following action names followed by an emoji:
        `pause` - Pauses your current Spotify player.
        `repeat` - Changes your current Spotify player to repeat current playlist.
        `repeatone` - Changes your current spotify player to repeat the track.
        `next` - Skips to the next track in queue.
        `previous` - Skips to the previous track in queue.
        `like` - Likes a song link or URI if it is inside the message reacted to.
        `volume_down` - Adjusts the volume of your Spotify player down 10%.
        `volume_up`- Adjusts the volume of your Spotify player up 10%.
        `volume_mute` - Mutes your Spotify player.
        `shuffle` - Shuffles your current Spotify player.
        `play` - Plays a song link or URI if it is inside the message reacted to.

        """
        added = {}
        async with self.config.user(ctx.author).listen_for() as current:
            for action, emoji in listen_for.items():
                if action not in emoji_handler.emojis:
                    continue
                custom_emoji = None
                with contextlib.suppress(commands.BadArgument):
                    custom_emoji = await commands.PartialEmojiConverter().convert(ctx, emoji)
                if custom_emoji:
                    current[str(custom_emoji)] = action
                    added[str(custom_emoji)] = action
                else:
                    with contextlib.suppress(discord.errors.HTTPException):
                        await ctx.message.add_reaction(str(emoji))
                        current[str(emoji)] = action
                        added[str(emoji)] = action
        msg = "I will now listen for the following emojis from you:\n"
        for emoji, action in added.items():
            msg += f"{emoji} -> {action}\n"
        await ctx.maybe_send_embed(msg)

    @spotify_set.command(name="remlisten")
    async def set_reaction_remove_listen(self, ctx: commands.Context, *emoji_or_name: str):
        """Set the bot to listen for specific emoji reactions on messages.

        If the message being reacted to has somthing valid to search
        for the bot will attempt to play the found search on spotify for you.

        `<listen_for>` Must be one of the following action names:
        `pause` - Pauses your current Spotify player.
        `repeat` - Changes your current Spotify player to repeat current playlist.
        `repeatone` - Changes your current spotify player to repeat the track.
        `next` - Skips to the next track in queue.
        `previous` - Skips to the previous track in queue.
        `like` - Likes a song link or URI if it is inside the message reacted to.
        `volume_down` - Adjusts the volume of your Spotify player down 10%.
        `volume_up`- Adjusts the volume of your Spotify player up 10%.
        `volume_mute` - Mutes your Spotify player.
        `shuffle` - Shuffles your current Spotify player.
        `play` - Plays a song link or URI if it is inside the message reacted to.

        """
        removed = []
        async with self.config.user(ctx.author).listen_for() as current:
            for name in emoji_or_name:
                if name in current:
                    action = current[name]
                    del current[name]
                    removed.append(f"{name} -> {action}")
                else:
                    to_rem = []
                    for emoji, action in current.items():
                        if name == action:
                            to_rem.append(emoji)
                            removed.append(f"{emoji} -> {action}")
                    if to_rem:
                        for emoji in to_rem:
                            del current[emoji]

        if not removed:
            return await ctx.send("None of the listed events were being listened for.")
        msg = ("I will no longer listen for emojis for the following events:\n{listen}").format(listen="\n".join(removed))

        await ctx.maybe_send_embed(msg)

    @spotify_set.command(name="showsettings", aliases=["settings"])
    @commands.mod_or_permissions(manage_messages=True)
    async def show_settings(self, ctx: commands.Context) -> None:
        """Show settings for menu timeouts."""
        delete_after = await self.config.guild(ctx.guild).delete_message_after()
        clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
        timeout = await self.config.guild(ctx.guild).menu_timeout()
        msg = f"Delete After: {delete_after}\nClear After: {clear_after}\nTimeout: {timeout}"
        await ctx.maybe_send_embed(msg)

    @spotify_set.command(name="showprivate")
    async def show_private(self, ctx: commands.Context, show_private: bool) -> None:
        """Set whether or not to show private playlists.

        This will also display your spotify username and a link to your
        profile if you use `;spotify me` command in public channels.

        """
        await self.config.user(ctx.author).show_private.set(show_private)
        msg = "I will show private playlists now." if show_private else "I will stop showing private playlists now."
        await ctx.send(msg)

    @spotify_set.command(name="clearreactions")
    @commands.mod_or_permissions(manage_messages=True)
    async def guild_clear_reactions(self, ctx: commands.Context, clear_after: bool) -> None:
        """Set whether or not to clear reactions after sending the message.

        Note: the bot requires manage messages for this to work

        """
        await self.config.guild(ctx.guild).clear_reactions_after.set(clear_after)
        msg = "I will now clear reactions after the menu has timed out.\n" if clear_after else "I will stop clearing reactions after the menu has timed out.\n"
        if not ctx.channel.permissions_for(ctx.me).manage_messages:
            msg += "I don't have manage messages permissions so this might not work as expected."
        await ctx.send(msg)

    @spotify_set.command(name="deletemessage")
    @commands.mod_or_permissions(manage_messages=True)
    async def guild_delete_message_after(self, ctx: commands.Context, delete_after: bool) -> None:
        """Set whether or not to delete the spotify message after timing out."""
        await self.config.guild(ctx.guild).delete_message_after.set(delete_after)
        msg = "I will now delete the menu message after timeout.\n" if delete_after else "I will stop deleting the menu message after timeout.\n"
        await ctx.send(msg)

    @spotify_set.command(name="menutimeout")
    @commands.mod_or_permissions(manage_messages=True)
    async def guild_menu_timeout(self, ctx: commands.Context, timeout: int) -> None:
        """Set the timeout time for spotify menus.

        `<timeout>` The time until the menu will timeout. This does not affect
        interacting with the menu.
        Note: This has a maximum of 10 minutes and a minimum of 30 seconds.

        """
        timeout = max(min(600, timeout), 30)
        await self.config.guild(ctx.guild).menu_timeout.set(timeout)
        msg = f"I will timeout menus after {timeout} seconds.\n"
        await ctx.send(msg)

    @spotify_set.command(name="resetemojis", aliases=["resetemoji"], hidden=True)
    @commands.is_owner()
    async def spotify_reset_emoji(self, ctx: commands.Context) -> None:
        """Resets the bot to use the default emojis."""
        await self.config.emojis.clear()
        emoji_handler.reload_emojis()
        await ctx.send("I will now use the default emojis.")

    @spotify_set.command(name="emojis", hidden=True)
    @commands.is_owner()
    async def spotify_emojis(self, ctx: commands.Context, *, new_emojis: ActionConverter):
        """Change the emojis used by the bot for various actions.

        `<new_emojis>` Is a space or comma separated list of name
        followed by emoji for example `;spotify set emojis playpause 😃`
        will then replace ⏯ usage with the 😃 emoji.

        Available name replacements:    `playpause` -> ⏯    `pause` -> ⏸
        `repeat` -> 🔁    `repeatone` -> 🔂    `next` -> ⏭    `previous`
        -> ⏮    `like` -> ♥    `fastforward` -> ⏩    `rewind` -> ⏪
        `volume_down` -> 🔉    `volume_up` -> 🔊    `volume_mute` -> 🔇
        `off` -> ❎    `playall` -> ⏏    `shuffle` -> 🔀    `back_left` ->
        ◀    `play` -> ▶    `queue` -> 🇶

        """
        emojis_changed = {}
        async with self.config.emojis() as emojis:
            for name, emoji in new_emojis.items():
                with contextlib.suppress(InvalidEmoji, discord.errors.HTTPException):
                    await ctx.message.add_reaction(str(emoji))
                    emoji_handler.replace_emoji(name, str(emoji))
                    emojis[name] = str(emoji)
                    emojis_changed[name] = str(emoji)
        if not emojis_changed:
            return await ctx.send("No emojis have been changed.")
        msg = "The following emojis have been replaced:\n"
        for name, emoji in emojis_changed.items():
            original = emoji_handler.default[name]
            msg += f"{original} -> {emoji}\n"
        await ctx.maybe_send_embed(msg)

    @spotify_set.command(name="scope", aliases=["scopes"], hidden=True)
    @commands.is_owner()
    async def spotify_api_scope(self, ctx: commands.Context, *scopes: ScopeConverter) -> None:
        """Set customized scope for what you want your bot to allow.

        Available options are: user-read-private user-top-read user-
        read-recently-played user-follow-read user-library-read user-
        read-currently-playing user-read-playback-state user-read-
        playback-position playlist-read-collaborative playlist-read-
        private user-follow-modify user-library-modify user-modify-
        playback-state playlist-modify-public playlist-modify-private

        You can find more information here:
        https://developer.spotify.com/documentation/general/guides/scopes/

        """
        added = []
        removed = []
        async with self.config.scopes() as current_scope:
            for scope in scopes:
                if scope in current_scope:
                    current_scope.remove(scope)
                    removed.append(scope)
                else:
                    current_scope.append(scope)
                    added.append(scope)
        add = humanize_list(added)
        rem = humanize_list(removed)
        msg = ""
        if add:
            msg += f"The following scopes were added: {add}\n"
        if rem:
            f"The following scopes were removed: {rem}\n"
        await ctx.maybe_send_embed(msg)

    @spotify_set.command(name="currentscope", aliases=["currentscopes"], hidden=True)
    @commands.is_owner()
    async def spotify_view_api_scope(self, ctx: commands.Context) -> None:
        """View the current scopes being requested."""
        scope = humanize_list(await self.config.scopes())
        await ctx.maybe_send_embed(f"Current scopes:\n{scope}")

    @spotify_set.command(name="creds", hidden=True)
    @commands.is_owner()
    async def spotify_api_credential_set(self, ctx: commands.Context) -> None:
        """Instructions to set the Spotify API tokens."""
        message = _(
            '1. Go to Spotify developers and log in with your Spotify account.\n(https://developer.spotify.com/dashboard/applications)\n2. Click "Create An App".\n3. Fill out the form provided with your app name, etc.\n4. When asked if you\'re developing commercial integration select "No".\n5. Accept the terms and conditions.\n6. Copy your client ID and your client secret into:\n`{prefix}set api spotify client_id <your_client_id_here> client_secret <your_client_secret_here>`\nYou may also provide `redirect_uri` in this command with a different redirect you would like to use but this is optional. the default redirect_uri is https://localhost/\n\nNote: The redirect URI Must be set in the Spotify Dashboard and must match either `https://localhost/` or the one you set with the `;set api` command',
        ).format(prefix=ctx.prefix)
        await ctx.maybe_send_embed(message)

    @spotify_set.command(name="forgetme")
    async def spotify_forgetme(self, ctx: commands.Context) -> None:
        """Forget all your spotify settings and credentials on the bot."""
        await self.config.user(ctx.author).clear()
        if ctx.author.id in self.dashboard_authed:
            self.dashboard_authed.remove(ctx.author.id)
        await ctx.send("All your spotify data deleted from my settings.")

    @spotify_com.command(name="me")
    async def spotify_me(self, ctx: commands.Context) -> None:
        """Shows your current Spotify Settings."""
        em = discord.Embed(color=discord.Colour(0x1DB954))
        em.set_author(name=f"{ctx.author.display_name} Spotify Profile", icon_url=ctx.author.avatar_url)
        cog_settings = await self.config.user(ctx.author).all()
        listen_emojis = "\n".join(f"{emoji} -> {action}" for emoji, action in cog_settings["listen_for"].items()) or "Nothing"
        show_private = cog_settings["show_private"]
        msg = "" + f"Watching for Emojis:\n{listen_emojis}\n"
        msg += f"Show Private Playlists: {show_private}\n"
        if not cog_settings["token"]:
            em.description = msg
            await ctx.send(embed=em)
            return
        user_token = await self.get_user_auth(ctx)
        if user_token:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.current_user()
        if show_private or isinstance(ctx.channel, discord.DMChannel):
            msg += f"Spotify Name: [{cur.display_name}](https://open.spotify.com/user/{cur.id})\nSubscription: {cur.product}\n"
        if isinstance(ctx.channel, discord.DMChannel):
            private = f"Country: {cur.country}\nSpotify ID: {cur.id}\nEmail: {cur.email}\n"
            em.add_field(name="Private Data", value=private)
        if cur.images:
            em.set_thumbnail(url=cur.images[0].url)
        em.description = msg
        await ctx.send(embed=em)

    @spotify_com.command(name="now", aliases=["np"])
    async def spotify_now(self, ctx: commands.Context, detailed: Optional[bool] = False, member: Optional[discord.Member] = None):
        """Displays your currently played spotify song.

        `[member]` Optional discord member to show their current spotify
        status if they're displaying it on Discord.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return

        refresh_rate = 220 if ctx.bot_owner else None
        async with ctx.typing():
            if member:
                if not [c for c in member.activities if c.type == discord.ActivityType.listening]:
                    return await ctx.send("That user is not currently listening to Spotify on Discord.")
                activity = [c for c in member.activities if c.type == discord.ActivityType.listening][0]
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    track = await user_spotify.track(activity.track_id)
            delete_after = False
            clear_after = True
            timeout = 500
        try:
            if member is None:
                page_source = SpotifyPages(user_token=user_token, redis=self.bot.redis, sender=self._sender, detailed=detailed)

            else:
                page_source = SpotifyTrackPages(items=[track], detailed=detailed)
            page_source.cog = self
            await SpotifyUserMenu(
                source=page_source,
                delete_message_after=delete_after,
                clear_reactions_after=clear_after,
                timeout=timeout,
                cog=self,
                user_token=user_token,
                auto_refresh_for=refresh_rate,
                use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
            ).start(ctx=ctx)
        except NotPlaying:
            await ctx.send("It appears you're not currently listening to Spotify.")
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")

    @spotify_com.command(name="like")
    async def spotify_like(self, ctx: commands.Context):
        """Like the currently playing track."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return await ctx.reply("You need to authorize me to interact with spotify.")
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            user_spotify.playback_currently_playing
            with user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await ctx.send("It appears you're not currently listening to Spotify.")

                elif cur.is_playing and not getattr(cur.item, "is_local", False):
                    track_id = cur.item.id
                    await user_spotify.saved_tracks_add([track_id])
                    await ctx.react_quietly("💚")

        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="share")
    async def spotify_share(self, ctx: commands.Context):
        """Tell the bot to play the users current song in their current voice
        channel.
        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                if not cur:
                    await ctx.send("It appears you're not currently listening to Spotify.")
                elif isinstance(cur.item, tekore.model.FullEpisode):
                    return await ctx.send("I cannot play podcasts from spotify.")
                elif cur.is_playing and not getattr(cur.item, "is_local", False):
                    msg = copy(ctx.message)
                    msg.content = f"{ctx.prefix}play {cur.item.uri}"
                    self.bot.dispatch("message", msg)
                    await ctx.tick()
                else:
                    return await ctx.send("You don't appear to be listening to something I can play in audio.")
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="search")
    async def spotify_search(self, ctx: commands.Context, detailed: Optional[bool] = False, search_type: Optional[SearchTypes] = "track", *, query: str):
        """Search Spotify for things to play.

        `[detailed=False]` Show detailed information for individual tracks.
        `[search_type=track]` The search type, available options are:
         - `track(s)`
         - `artist(s)`
         - `album(s)`
         - `playlist(s)`
         - `show(s)`
         - `episode(s)`
        `<query>` What you want to search for.

        """
        async with ctx.typing():
            search_types = {
                "track": SpotifyTrackPages,
                "artist": SpotifyArtistPages,
                "album": SpotifyAlbumPages,
                "episode": SpotifyEpisodePages,
                "playlist": SpotifyPlaylistPages,
                "show": SpotifyShowPages,
            }
            user_token = await self.get_user_auth(ctx)
            if not user_token:
                return
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                search = await user_spotify.search(query, (search_type,), "from_token", limit=50)
                items = search[0].items
            if not search[0].items:
                return await ctx.send(f"No {search_type} could be found matching that query.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
        await SpotifySearchMenu(
            source=search_types[search_type](items=items, detailed=detailed),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    # @spotify_com.command(name="genres", aliases=["genre"])
    #
    # async def spotify_genres(self, ctx: commands.Context):
    #     """
    #     Display all available genres for the recommendations.
    #     """
    #         return await ctx.send(
    #                 " details."
    #             ).format(prefix=ctx.clean_prefix)
    #     await ctx.maybe_send_embed(

    # @spotify_com.command(name="recommendations", aliases=["recommend", "recommendation"])
    #
    # async def spotify_recommendations(self, ctx: commands.Context, detailed: Optional[bool] = False, *, recommendations: RecommendationsConverter):
    #     """
    #     Get Spotify Recommendations.

    #     `<recommendations>` Requires at least 1 of the following matching objects:
    #      - `genre` Must be a valid genre type. Do `;spotify genres` to see what's available.
    #      - `tracks` Any spotify URL or URI leading to tracks will be added to the seed
    #      - `artists` Any spotify URL or URI leading to artists will be added to the seed

    #      The following parameters also exist and must include some additional parameter:
    #      - `acousticness` + a value from 0-100
    #      - `danceability` + a value from 0-100
    #      - `duration_ms` the duration target of the tracks
    #      - `energy` + a value from 0-100
    #      - `instrumentalness` + a value from 0-100
    #      - `key` A value from 0-11 representing Pitch Class notation
    #      - `liveness` + a value from 0-100
    #      - `loudness` + A value from -60 to 0 represending dB
    #      - `mode` + either major or minor
    #      - `popularity` + a value from 0-100
    #      - `speechiness` + a value from 0-100
    #      - `tempo` + the tempo in BPM
    #      - `time_signature` + the measure of bars e.g. `3` for `3/4` or `6/8`
    #      - `valence` + a value from 0-100

    #     """
    #     async with ctx.typing():
    #         if not user_token:
    #         with user_spotify.token_as(user_token):
    #         if not items:
    #         if ctx.guild:
    #     await SpotifySearchMenu(
    #     ).start(ctx=ctx)

    @spotify_com.command(name="recent")
    async def spotify_recently_played(self, ctx: commands.Context, detailed: Optional[bool] = False):
        """Displays your most recently played songs on Spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    search = await user_spotify.playback_recently_played(limit=50)
                    tracks = search.items
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
        await SpotifySearchMenu(
            source=SpotifyRecentSongPages(tracks=tracks, detailed=detailed),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_com.command(name="toptracks")
    async def top_tracks(self, ctx: commands.Context):
        """List your top tracks on spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.current_user_top_tracks(limit=50)
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            tracks = cur.items
        await SpotifyBaseMenu(
            source=SpotifyTopTracksPages(tracks),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_com.command(name="topartists")
    async def top_artists(self, ctx: commands.Context):
        """List your top tracks on spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.current_user_top_artists(limit=50)
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            artists = cur.items
        await SpotifyBaseMenu(
            source=SpotifyTopArtistsPages(artists),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_com.command(name="new")
    async def spotify_new(self, ctx: commands.Context):
        """List new releases on Spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                playlists = await user_spotify.new_releases(limit=50)
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            playlist_list = playlists.items
        await SpotifySearchMenu(
            source=SpotifyNewPages(playlist_list),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_com.command(name="pause")
    async def spotify_pause(self, ctx: commands.Context):
        """Pauses spotify for you."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                await user_spotify.playback_pause()
            await ctx.react_quietly(emoji_handler.get_emoji("pause", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="resume")
    async def spotify_resume(self, ctx: commands.Context):
        """Resumes spotify for you."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                if not cur or not cur.is_playing:
                    await user_spotify.playback_resume()
                else:
                    return await ctx.send("You are already playing music on Spotify.")
            await ctx.react_quietly(emoji_handler.get_emoji("play", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="skip", aliases=["next"])
    async def spotify_next(self, ctx: commands.Context):
        """Skips to the next track in queue on Spotify."""
        await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                await user_spotify.playback_next()
            await ctx.message.delete(delay=2)
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="previous", aliases=["prev"])
    async def spotify_previous(self, ctx: commands.Context):
        """Skips to the previous track in queue on Spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                await user_spotify.playback_previous()
            await ctx.react_quietly(emoji_handler.get_emoji("previous", ctx.channel.permissions_for(ctx.me).use_external_emojis))
            await ctx.message.delete(delay=2)
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="play", aliases=["link"])
    async def spotify_play(self, ctx: commands.Context, *, url_or_playlist_name: Optional[str] = ""):
        """Play a track, playlist, or album on Spotify."""
        url_or_playlist_name = url_or_playlist_name.replace("🧑‍🎨", ":artist:")
        # because discord will replace this in URI's automatically 🙄
        SPOTIFY_RE.finditer(url_or_playlist_name)
        tracks = []
        uri_type = None
        ident = None
        new_uri = ""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with contextlib.suppress(ConversionError):
                uri_type, ident = from_url(url_or_playlist_name)
                new_uri = url_or_playlist_name
            with user_spotify.token_as(user_token):
                if new_uri:
                    if uri_type in ("artist", "album", "playlist"):
                        await user_spotify.playback_start_context(new_uri)
                        await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                        return
                    else:
                        tracks.append(ident)
                if url_or_playlist_name:
                    cur = await user_spotify.followed_playlists(limit=50)
                    playlists = cur.items
                    while len(playlists) < cur.total:
                        new = await user_spotify.followed_playlists(limit=50, offset=len(playlists))
                        for p in new.items:
                            playlists.append(p)
                    for playlist in playlists:
                        if url_or_playlist_name.lower() in playlist.name.lower():
                            await user_spotify.playback_start_context(playlist.uri)
                            await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                            return
                    saved_tracks = await user_spotify.saved_tracks(limit=50)
                    for track in saved_tracks.items:
                        joined = ", ".join(a.name for a in track.track.artists)
                        if url_or_playlist_name.lower() in track.track.name.lower() or url_or_playlist_name.lower() in joined:
                            await user_spotify.playback_start_tracks([track.track.id])
                            await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                            return
                if not tracks:
                    s = await user_spotify.search(url_or_playlist_name)
                    s: FullTrackPaging = s[0]
                    tracks.append(s.items[0].id)

                if tracks:
                    await user_spotify.playback_start_tracks(tracks)
                    await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                    return

                await ctx.send("I could not find any URL's or matching playlist names.")
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            log.opt(exception=True).debug("Error playing song")
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="queue", aliases=["q"])
    async def spotify_queue_add(self, ctx: commands.Context, *, songs: str):
        """Queue a song to play next in Spotify.

        `<songs>` is one or more spotify URL or URI leading to a single
        track that will be added to your current queue

        """
        user_token = await self.get_user_auth(ctx)
        tracks = []
        if not user_token:
            return
        if matches := SPOTIFY_RE.match(songs):
            tracks.extend([f"spotify:{song.group(2)}:{song.group(3)}" for song in matches if song.group(2) == "track"])

        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                if not tracks:
                    s = await user_spotify.search(songs)
                    s: FullTrackPaging = s[0]
                    tracks.append(s.items[0].uri)
                log.info(tracks)
                for uri in tracks:
                    await user_spotify.playback_queue_add(uri)
            await ctx.react_quietly(emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="repeat")
    async def spotify_repeat(self, ctx: commands.Context, state: Optional[str]):
        """Repeats your current song on spotify.

        `<state>` must accept one of `off`, `track`, or `context`.

        """
        if state and state.lower() not in ["off", "track", "context"]:
            return await ctx.send("Repeat must accept either `off`, `track`, or `context`.")
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                if state:
                    lookup = {"off": "off", "context": "repeat", "track": "repeatone"}
                    emoji = emoji_handler.get_emoji(lookup[state.lower()], ctx.channel.permissions_for(ctx.me).use_external_emojis)
                else:
                    cur = await user_spotify.playback()
                    if not cur:
                        return await ctx.send("I could not find an active device to play songs on.")
                    if cur.repeat_state == "off":
                        state = "context"
                        emoji = emoji_handler.get_emoji("repeat", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                    if cur.repeat_state == "context":
                        state = "track"
                        emoji = emoji_handler.get_emoji("repeatone", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                    if cur.repeat_state == "track":
                        state = "off"
                        emoji = emoji_handler.get_emoji("off", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                await user_spotify.playback_repeat(str(state).lower())
            await ctx.react_quietly(emoji)
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="shuffle")
    async def spotify_shuffle(self, ctx: commands.Context, state: Optional[bool] = None):
        """Shuffles your current song list.

        `<state>` either true or false. Not providing this will toggle
        the current setting.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                if state is None:
                    cur = await user_spotify.playback()
                    if not cur:
                        await ctx.send("I could not find an active device to play songs on.")
                    state = not cur.shuffle_state
                await user_spotify.playback_shuffle(state)
            await ctx.react_quietly(emoji_handler.get_emoji("shuffle", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="seek")
    async def spotify_seek(self, ctx: commands.Context, seconds: Union[int, str]):
        """Seek to a specific point in the current song.

        `<seconds>` Accepts seconds or a value formatted like 00:00:00
        (`hh:mm:ss`) or 00:00 (`mm:ss`).

        """
        try:
            int(seconds)
            abs_position = False
        except ValueError:
            abs_position = True
            seconds = time_convert(seconds)
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                now = cur.progress_ms
                total = cur.item.duration_ms
                emoji = emoji_handler.get_emoji("fastforward", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                log.debug(seconds)
                to_seek = seconds * 1000 if abs_position else seconds * 1000 + now
                if to_seek < now:
                    emoji = emoji_handler.get_emoji("rewind", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                if to_seek > total:
                    emoji = emoji_handler.get_emoji("next", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                await user_spotify.playback_seek(to_seek)
            await ctx.react_quietly(emoji)
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.command(name="volume", aliases=["vol"])
    async def spotify_volume(self, ctx: commands.Context, volume: Union[int, str]):
        """Set your spotify volume percentage.

        `<volume>` a number between 0 and 100 for volume percentage.

        """
        volume = max(min(100, volume), 0)  # constrains volume to be within 100
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.playback()
                await user_spotify.playback_volume(volume)
                if volume == 0:
                    await ctx.react_quietly(emoji_handler.get_emoji("volume_mute", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                elif cur and volume > cur.device.volume_percent:
                    await ctx.react_quietly(emoji_handler.get_emoji("volume_up", ctx.channel.permissions_for(ctx.me).use_external_emojis))
                else:
                    await ctx.react_quietly(emoji_handler.get_emoji("volume_down", ctx.channel.permissions_for(ctx.me).use_external_emojis))
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_com.group(name="device")
    async def spotify_device(self, ctx: commands.Context) -> None:
        """Spotify device commands."""

    @spotify_device.command(name="transfer")
    async def spotify_device_transfer(self, ctx: commands.Context, *, device_name: Optional[str] = None):
        """Change the currently playing spotify device.

        `<device_name>` The name of the device you want to switch to.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            is_playing = False
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                devices = await user_spotify.playback_devices()
                now = await user_spotify.playback()
                if now and now.is_playing:
                    is_playing = True
            new_device = None
            if device_name:
                for d in devices:
                    if device_name.lower() in d.name.lower():
                        log.debug(f"Transferring playback to {d.name}")
                        new_device = d
            else:
                new_device = await self.spotify_pick_device(ctx, devices)
            if not new_device:
                return await ctx.send("I will not transfer spotify playback for you.")
            with user_spotify.token_as(user_token):
                await user_spotify.playback_transfer(new_device.id, is_playing)
            await ctx.tick()
        except tekore.Unauthorised as e:
            log.opt(exception=e).debug("Error transferring playback")
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    async def spotify_pick_device(self, ctx: commands.Context, devices: tekore.model.ModelList[tekore.model.Device]) -> Optional[tekore.model.Device]:
        """Allows a user to pick the device via reactions or message to simply
        transfer devices.
        """
        devices = devices[:9]
        devices_msg = "React with the device you want to transfer playback to:\n"
        for c, d in enumerate(devices):
            devices_msg += f"{c+1}. `{d.name}` - {d.type} - {d.volume_percent}% "
            if d.is_active:
                devices_msg += emoji_handler.get_emoji("playpause", ctx.channel.permissions_for(ctx.me).use_external_emojis)
            devices_msg += "\n"
        msg = await ctx.maybe_send_embed(devices_msg)
        emojis = ReactionPredicate.NUMBER_EMOJIS[1 : len(devices) + 1]
        start_adding_reactions(msg, emojis)
        pred = ReactionPredicate.with_emojis(emojis, msg)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred)
        except TimeoutError:
            return None
        else:
            return devices[pred.result]

    @spotify_device.command(name="list")
    async def spotify_device_list(self, ctx: commands.Context):
        """List all available devices for Spotify."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                devices = await user_spotify.playback_devices()
                await user_spotify.playback()
            devices_msg = f"{ctx.author.display_name}'s Spotify Devices:\n"
            for c, d in enumerate(devices):
                devices_msg += f"{c+1}. `{d.name}` - {d.type} - {d.volume_percent}% "
                if d.is_active:
                    devices_msg += emoji_handler.get_emoji("playpause", ctx.channel.permissions_for(ctx.me).use_external_emojis)
                devices_msg += "\n"
            await ctx.maybe_send_embed(devices_msg)
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_playlist.command(name="featured")
    async def spotify_playlist_featured(self, ctx: commands.Context):
        """List your Spotify featured Playlists."""
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    playlists = await user_spotify.featured_playlists(limit=50)
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            playlist_list = playlists[1].items
        await SpotifySearchMenu(
            source=SpotifyNewPages(playlist_list),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_playlist.command(name="list", aliases=["ls"])
    async def playlist_playlist_list(self, ctx: commands.Context):
        """List your Spotify Playlists.

        If this command is done in DM with the bot it will show private
        playlists otherwise this will not display private playlists
        unless showprivate has been toggled on.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.followed_playlists(limit=50)
                    playlists = cur.items
                    while len(playlists) < cur.total:
                        new = await user_spotify.followed_playlists(limit=50, offset=len(playlists))
                        for p in new.items:
                            playlists.append(p)
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            show_private = await self.config.user(ctx.author).show_private() or isinstance(ctx.channel, discord.DMChannel)
            playlist_list = playlists if show_private else [p for p in playlists if p.public is not False]
        await SpotifyBaseMenu(
            source=SpotifyPlaylistsPages(playlist_list),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_playlist.command(name="view")
    async def spotify_playlist_view(self, ctx: commands.Context):
        """View details about your spotify playlists.

        If this command is done in DM with the bot it will show private
        playlists otherwise this will not display private playlists
        unless showprivate has been toggled on.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        async with ctx.typing():
            try:
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    cur = await user_spotify.followed_playlists(limit=50)
                    playlists = cur.items
                    while len(playlists) < cur.total:
                        new = await user_spotify.followed_playlists(limit=50, offset=len(playlists))
                        for p in new.items:
                            playlists.append(p)
            except tekore.Unauthorised:
                return await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
            show_private = await self.config.user(ctx.author).show_private() or isinstance(ctx.channel, discord.DMChannel)
            show_private = await self.config.user(ctx.author).show_private() or isinstance(ctx.channel, discord.DMChannel)
            playlist_list = playlists if show_private else [p for p in playlists if p.public is not False]
        await SpotifySearchMenu(
            source=SpotifyPlaylistPages(playlist_list, False),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)

    @spotify_playlist.command(name="create")
    async def spotify_playlist_create(self, ctx: commands.Context, name: str, public: Optional[bool] = False, *, description: Optional[str] = ""):
        """Create a Spotify Playlist.

        `<name>` The name of the newly created playlist `[public]`
        Wheter or not the playlist should be public, defaults to False.
        `[description]` The description of the playlist you're making.

        """
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                user = await user_spotify.current_user()
                await user_spotify.playlist_create(user.id, name, public, description)
                await ctx.tick()
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_playlist.command(name="add")
    async def spotify_playlist_add(self, ctx: commands.Context, name: str, *to_add: SpotifyURIConverter):
        """Add 1 (or more) tracks to a spotify playlist.

        `<name>` The name of playlist you want to add songs to
        `<to_remove>` The song links or URI's you want to add

        """
        tracks = []
        new_uri = ""
        for match in to_add:
            new_uri = f"spotify:{match.group(2)}:{match.group(3)}"
            if match.group(2) == "track":
                tracks.append(new_uri)
        if not tracks:
            return await ctx.send("You did not provide any tracks for me to add to the playlist.")
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.followed_playlists(limit=50)
                playlists = cur.items
                while len(playlists) < cur.total:
                    new = await user_spotify.followed_playlists(limit=50, offset=len(playlists))
                    for p in new.items:
                        playlists.append(p)
                for playlist in playlists:
                    if name.lower() == playlist.name.lower():
                        await user_spotify.playlist_add(playlist.id, tracks)
                        await ctx.tick()
                        return
            await ctx.send(f"I could not find a playlist matching {name}.")
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_playlist.command(name="remove")
    async def spotify_playlist_remove(self, ctx: commands.Context, name: str, *to_remove: SpotifyURIConverter):
        """Remove 1 (or more) tracks to a spotify playlist.

        `<name>` The name of playlist you want to remove songs from
        `<to_remove>` The song links or URI's you want to have removed

        """
        tracks = []
        new_uri = ""
        for match in to_remove:
            new_uri = f"spotify:{match.group(2)}:{match.group(3)}"
            if match.group(2) == "track":
                tracks.append(new_uri)
        if not tracks:
            return await ctx.send("You did not provide any tracks for me to add to the playlist.")
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                cur = await user_spotify.followed_playlists(limit=50)
                playlists = cur.items
                while len(playlists) < cur.total:
                    new = await user_spotify.followed_playlists(limit=50, offset=len(playlists))
                    for p in new.items:
                        playlists.append(p)
                for playlist in playlists:
                    if name.lower() == playlist.name.lower():
                        await user_spotify.playlist_remove(playlist.id, tracks)
                        await ctx.tick()
                        return
            await ctx.send(f"I could not find a playlist matching {name}.")
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_playlist.command(name="follow")
    async def spotify_playlist_follow(self, ctx: commands.Context, public: Optional[bool] = False, *to_follow: SpotifyURIConverter):
        """Add a playlist to your spotify library.

        `[public]` Whether or not the followed playlist should be public
        after `<to_follow>` The song links or URI's you want to have
        removed

        """
        tracks = [match.group(3) for match in to_follow if match.group(2) == "playlist"]

        if not tracks:
            return await ctx.send("You did not provide any playlists for me to add to your library.")
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                for playlist in tracks:
                    await user_spotify.playlist_follow(playlist, public)
                await ctx.tick()
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_artist.command(name="follow")
    async def spotify_artist_follow(self, ctx: commands.Context, *to_follow: SpotifyURIConverter):
        """Add an artist to your spotify library.

        `<to_follow>` The song links or URI's you want to have removed

        """
        tracks = [match.group(3) for match in to_follow if match.group(2) == "artist"]
        user_token = await self.get_user_auth(ctx)
        if not user_token:
            return
        try:
            user_spotify = tekore.Spotify(sender=self._sender)
            with user_spotify.token_as(user_token):
                for playlist in tracks:
                    await user_spotify.artist_follow(playlist)
                await ctx.tick()
        except tekore.Unauthorised:
            await ctx.send("I am not authorized to perform this action for you.")
        except tekore.NotFound:
            await ctx.send("I could not find an active device to play songs on.")
        except tekore.Forbidden as e:
            if "non-premium" in str(e):
                redis = get_redis()
                if not await redis.ratelimited(f"sp_notify:{ctx.author.id}", 1, 90):
                    await ctx.send("This action is prohibited for non-premium users.")
            else:
                await ctx.send("I couldn't perform that action for you.")
        except tekore.HTTPError:
            log.exception("Error grabing user info from spotify")
            await ctx.send("Unknown error. This has been reported and should be resolved soon.")

    @spotify_artist.command(name="albums", aliases=["album"])
    async def spotify_artist_albums(self, ctx: commands.Context, *to_follow: SpotifyURIConverter):
        """View an artists albums.

        `<to_follow>` The artis links or URI's you want to view the
        albums of

        """
        async with ctx.typing():
            tracks = [match.group(3) for match in to_follow if match.group(2) == "artist"]
            if not tracks:
                return await ctx.send("You did not provide an artist link or URI.")
            try:
                user_token = await self.get_user_auth(ctx)
                if not user_token:
                    return
                user_spotify = tekore.Spotify(sender=self._sender)
                with user_spotify.token_as(user_token):
                    search = await user_spotify.artist_albums(tracks[0], limit=50)
                    tracks = search.items
            except tekore.Unauthorised:
                await ctx.send("I am not authorized to perform this action for you.")
            if ctx.guild:
                delete_after = await self.config.guild(ctx.guild).delete_message_after()
                clear_after = await self.config.guild(ctx.guild).clear_reactions_after()
                timeout = await self.config.guild(ctx.guild).menu_timeout()
            else:
                delete_after, clear_after, timeout = False, True, 120
        await SpotifySearchMenu(
            source=SpotifyAlbumPages(tracks, False),
            delete_message_after=delete_after,
            clear_reactions_after=clear_after,
            timeout=timeout,
            cog=self,
            user_token=user_token,
            use_external=ctx.channel.permissions_for(ctx.me).use_external_emojis,
        ).start(ctx=ctx)
