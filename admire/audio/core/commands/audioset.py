from __future__ import annotations

import contextlib
import os
import tarfile
from typing import Union

import discord
import lavalink
from melaniebot.core import bank, commands
from melaniebot.core.data_manager import cog_data_path
from melaniebot.core.utils.chat_formatting import box, humanize_number
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu, start_adding_reactions
from melaniebot.core.utils.predicates import MessagePredicate, ReactionPredicate

from audio.audio_dataclasses import LocalPath
from audio.converters import ScopeParser
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass, PlaylistConverter, __version__
from audio.errors import MissingGuild, TooManyMatches
from audio.utils import CacheLevel, PlaylistScope, has_internal_server


def _(x):
    return x


class AudioSetCommands(MixinMeta, metaclass=CompositeMetaClass):
    @commands.group(name="audioset")
    async def command_audioset(self, ctx: commands.Context) -> None:
        """Music configuration options."""

    @command_audioset.group(name="restrictions")
    @commands.mod_or_permissions(manage_guild=True)
    async def command_audioset_perms(self, ctx: commands.Context) -> None:
        """Manages the keyword whitelist and blacklist."""

    @commands.is_owner()
    @command_audioset_perms.group(name="global")
    async def command_audioset_perms_global(self, ctx: commands.Context) -> None:
        """Manages the global keyword whitelist/blacklist."""

    @command_audioset_perms_global.group(name="whitelist")
    async def command_audioset_perms_global_whitelist(self, ctx: commands.Context) -> None:
        """Manages the global keyword whitelist."""

    @command_audioset_perms_global_whitelist.command(name="add")
    async def command_audioset_perms_global_whitelist_add(self, ctx: commands.Context, *, keyword: str):
        """Adds a keyword to the whitelist.

        If anything is added to whitelist, it will blacklist everything
        else.

        """
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = False
        async with self.config.url_keyword_whitelist() as whitelist:
            if keyword in whitelist:
                exists = True
            else:
                whitelist.append(keyword)
        if exists:
            return await self.send_embed_msg(ctx, title="Keyword already in the whitelist.")
        else:
            return await self.send_embed_msg(
                ctx,
                title="Whitelist Modified",
                description=("Added `{whitelisted}` to the whitelist.").format(whitelisted=keyword),
            )

    @command_audioset_perms_global_whitelist.command(name="list")
    async def command_audioset_perms_global_whitelist_list(self, ctx: commands.Context):
        """List all keywords added to the whitelist."""
        whitelist = await self.config.url_keyword_whitelist()
        if not whitelist:
            return await self.send_embed_msg(ctx, title="Nothing in the whitelist.")
        whitelist.sort()
        text = ""
        total = len(whitelist)
        pages = []
        for i, entry in enumerate(whitelist, 1):
            text += f"{i}. [{entry}]"
            if i != total:
                text += "\n"
                if i % 10 == 0:
                    pages.append(box(text, lang="ini"))
                    text = ""
            else:
                pages.append(box(text, lang="ini"))
        embed_colour = await ctx.embed_colour()
        pages = [discord.Embed(title="Global Whitelist", description=page, colour=embed_colour) for page in pages]

        await menu(ctx, pages, DEFAULT_CONTROLS)

    @command_audioset_perms_global_whitelist.command(name="clear")
    async def command_audioset_perms_global_whitelist_clear(self, ctx: commands.Context):
        """Clear all keywords from the whitelist."""
        whitelist = await self.config.url_keyword_whitelist()
        if not whitelist:
            return await self.send_embed_msg(ctx, title="Nothing in the whitelist.")
        await self.config.url_keyword_whitelist.clear()
        return await self.send_embed_msg(ctx, title="Whitelist Modified", description="All entries have been removed from the whitelist.")

    @command_audioset_perms_global_whitelist.command(name="delete", aliases=["del", "remove"])
    async def command_audioset_perms_global_whitelist_delete(self, ctx: commands.Context, *, keyword: str):
        """Removes a keyword from the whitelist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = True
        async with self.config.url_keyword_whitelist() as whitelist:
            if keyword not in whitelist:
                exists = False
            else:
                whitelist.remove(keyword)
        return (
            await self.send_embed_msg(ctx, title="Whitelist Modified", description=("Removed `{whitelisted}` from the whitelist.").format(whitelisted=keyword))
            if exists
            else await self.send_embed_msg(ctx, title="Keyword already in the whitelist.")
        )

    @command_audioset_perms_global.group(name="blacklist")
    async def command_audioset_perms_global_blacklist(self, ctx: commands.Context) -> None:
        """Manages the global keyword blacklist."""

    @command_audioset_perms_global_blacklist.command(name="add")
    async def command_audioset_perms_global_blacklist_add(self, ctx: commands.Context, *, keyword: str):
        """Adds a keyword to the blacklist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = False
        async with self.config.url_keyword_blacklist() as blacklist:
            if keyword in blacklist:
                exists = True
            else:
                blacklist.append(keyword)
        if exists:
            return await self.send_embed_msg(ctx, title="Keyword already in the blacklist.")
        else:
            return await self.send_embed_msg(
                ctx,
                title="Blacklist Modified",
                description=("Added `{blacklisted}` to the blacklist.").format(blacklisted=keyword),
            )

    @command_audioset_perms_global_blacklist.command(name="list")
    async def command_audioset_perms_global_blacklist_list(self, ctx: commands.Context):
        """List all keywords added to the blacklist."""
        blacklist = await self.config.url_keyword_blacklist()
        if not blacklist:
            return await self.send_embed_msg(ctx, title="Nothing in the blacklist.")
        blacklist.sort()
        text = ""
        total = len(blacklist)
        pages = []
        for i, entry in enumerate(blacklist, 1):
            text += f"{i}. [{entry}]"
            if i != total:
                text += "\n"
                if i % 10 == 0:
                    pages.append(box(text, lang="ini"))
                    text = ""
            else:
                pages.append(box(text, lang="ini"))
        embed_colour = await ctx.embed_colour()
        pages = [discord.Embed(title="Global Blacklist", description=page, colour=embed_colour) for page in pages]

        await menu(ctx, pages, DEFAULT_CONTROLS)

    @command_audioset_perms_global_blacklist.command(name="clear")
    async def command_audioset_perms_global_blacklist_clear(self, ctx: commands.Context):
        """Clear all keywords added to the blacklist."""
        blacklist = await self.config.url_keyword_blacklist()
        if not blacklist:
            return await self.send_embed_msg(ctx, title="Nothing in the blacklist.")
        await self.config.url_keyword_blacklist.clear()
        return await self.send_embed_msg(ctx, title="Blacklist Modified", description="All entries have been removed from the blacklist.")

    @command_audioset_perms_global_blacklist.command(name="delete", aliases=["del", "remove"])
    async def command_audioset_perms_global_blacklist_delete(self, ctx: commands.Context, *, keyword: str):
        """Removes a keyword from the blacklist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = True
        async with self.config.url_keyword_blacklist() as blacklist:
            if keyword not in blacklist:
                exists = False
            else:
                blacklist.remove(keyword)
        return (
            await self.send_embed_msg(ctx, title="Blacklist Modified", description=("Removed `{blacklisted}` from the blacklist.").format(blacklisted=keyword))
            if exists
            else await self.send_embed_msg(ctx, title="Keyword is not in the blacklist.")
        )

    @command_audioset_perms.group(name="whitelist")
    @commands.guild_only()
    async def command_audioset_perms_whitelist(self, ctx: commands.Context) -> None:
        """Manages the keyword whitelist."""

    @command_audioset_perms_whitelist.command(name="add")
    async def command_audioset_perms_whitelist_add(self, ctx: commands.Context, *, keyword: str):
        """Adds a keyword to the whitelist.

        If anything is added to whitelist, it will blacklist everything
        else.

        """
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = False
        async with self.config.guild(ctx.guild).url_keyword_whitelist() as whitelist:
            if keyword in whitelist:
                exists = True
            else:
                whitelist.append(keyword)
        if exists:
            return await self.send_embed_msg(ctx, title="Keyword already in the whitelist.")
        else:
            return await self.send_embed_msg(
                ctx,
                title="Whitelist Modified",
                description=("Added `{whitelisted}` to the whitelist.").format(whitelisted=keyword),
            )

    @command_audioset_perms_whitelist.command(name="list")
    async def command_audioset_perms_whitelist_list(self, ctx: commands.Context):
        """List all keywords added to the whitelist."""
        whitelist = await self.config.guild(ctx.guild).url_keyword_whitelist()
        if not whitelist:
            return await self.send_embed_msg(ctx, title="Nothing in the whitelist.")
        whitelist.sort()
        text = ""
        total = len(whitelist)
        pages = []
        for i, entry in enumerate(whitelist, 1):
            text += f"{i}. [{entry}]"
            if i != total:
                text += "\n"
                if i % 10 == 0:
                    pages.append(box(text, lang="ini"))
                    text = ""
            else:
                pages.append(box(text, lang="ini"))
        embed_colour = await ctx.embed_colour()
        pages = [discord.Embed(title="Whitelist", description=page, colour=embed_colour) for page in pages]

        await menu(ctx, pages, DEFAULT_CONTROLS)

    @command_audioset_perms_whitelist.command(name="clear")
    async def command_audioset_perms_whitelist_clear(self, ctx: commands.Context):
        """Clear all keywords from the whitelist."""
        whitelist = await self.config.guild(ctx.guild).url_keyword_whitelist()
        if not whitelist:
            return await self.send_embed_msg(ctx, title="Nothing in the whitelist.")
        await self.config.guild(ctx.guild).url_keyword_whitelist.clear()
        return await self.send_embed_msg(ctx, title="Whitelist Modified", description="All entries have been removed from the whitelist.")

    @command_audioset_perms_whitelist.command(name="delete", aliases=["del", "remove"])
    async def command_audioset_perms_whitelist_delete(self, ctx: commands.Context, *, keyword: str):
        """Removes a keyword from the whitelist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = True
        async with self.config.guild(ctx.guild).url_keyword_whitelist() as whitelist:
            if keyword not in whitelist:
                exists = False
            else:
                whitelist.remove(keyword)
        return (
            await self.send_embed_msg(ctx, title="Whitelist Modified", description=("Removed `{whitelisted}` from the whitelist.").format(whitelisted=keyword))
            if exists
            else await self.send_embed_msg(ctx, title="Keyword already in the whitelist.")
        )

    @command_audioset_perms.group(name="blacklist")
    @commands.guild_only()
    async def command_audioset_perms_blacklist(self, ctx: commands.Context) -> None:
        """Manages the keyword blacklist."""

    @command_audioset_perms_blacklist.command(name="add")
    async def command_audioset_perms_blacklist_add(self, ctx: commands.Context, *, keyword: str):
        """Adds a keyword to the blacklist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = False
        async with self.config.guild(ctx.guild).url_keyword_blacklist() as blacklist:
            if keyword in blacklist:
                exists = True
            else:
                blacklist.append(keyword)
        if exists:
            return await self.send_embed_msg(ctx, title="Keyword already in the blacklist.")
        else:
            return await self.send_embed_msg(
                ctx,
                title="Blacklist Modified",
                description=("Added `{blacklisted}` to the blacklist.").format(blacklisted=keyword),
            )

    @command_audioset_perms_blacklist.command(name="list")
    async def command_audioset_perms_blacklist_list(self, ctx: commands.Context):
        """List all keywords added to the blacklist."""
        blacklist = await self.config.guild(ctx.guild).url_keyword_blacklist()
        if not blacklist:
            return await self.send_embed_msg(ctx, title="Nothing in the blacklist.")
        blacklist.sort()
        text = ""
        total = len(blacklist)
        pages = []
        for i, entry in enumerate(blacklist, 1):
            text += f"{i}. [{entry}]"
            if i != total:
                text += "\n"
                if i % 10 == 0:
                    pages.append(box(text, lang="ini"))
                    text = ""
            else:
                pages.append(box(text, lang="ini"))
        embed_colour = await ctx.embed_colour()
        pages = [discord.Embed(title="Blacklist", description=page, colour=embed_colour) for page in pages]

        await menu(ctx, pages, DEFAULT_CONTROLS)

    @command_audioset_perms_blacklist.command(name="clear")
    async def command_audioset_perms_blacklist_clear(self, ctx: commands.Context):
        """Clear all keywords added to the blacklist."""
        blacklist = await self.config.guild(ctx.guild).url_keyword_blacklist()
        if not blacklist:
            return await self.send_embed_msg(ctx, title="Nothing in the blacklist.")
        await self.config.guild(ctx.guild).url_keyword_blacklist.clear()
        return await self.send_embed_msg(ctx, title="Blacklist Modified", description="All entries have been removed from the blacklist.")

    @command_audioset_perms_blacklist.command(name="delete", aliases=["del", "remove"])
    async def command_audioset_perms_blacklist_delete(self, ctx: commands.Context, *, keyword: str):
        """Removes a keyword from the blacklist."""
        keyword = keyword.lower().strip()
        if not keyword:
            return await ctx.send_help()
        exists = True
        async with self.config.guild(ctx.guild).url_keyword_blacklist() as blacklist:
            if keyword not in blacklist:
                exists = False
            else:
                blacklist.remove(keyword)
        return (
            await self.send_embed_msg(ctx, title="Blacklist Modified", description=("Removed `{blacklisted}` from the blacklist.").format(blacklisted=keyword))
            if exists
            else await self.send_embed_msg(ctx, title="Keyword is not in the blacklist.")
        )

    @command_audioset.group(name="autoplay")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def command_audioset_autoplay(self, ctx: commands.Context) -> None:
        """Change auto-play setting."""

    @command_audioset_autoplay.command(name="toggle")
    async def command_audioset_autoplay_toggle(self, ctx: commands.Context) -> None:
        """Toggle auto-play when there no songs in queue."""
        autoplay = await self.config.guild(ctx.guild).auto_play()
        repeat = await self.config.guild(ctx.guild).repeat()
        disconnect = await self.config.guild(ctx.guild).disconnect()
        msg = ("Auto-play when queue ends: {true_or_false}.").format(true_or_false="Disabled" if autoplay else ("Enabled"))

        await self.config.guild(ctx.guild).auto_play.set(not autoplay)
        if autoplay is not True and repeat is True:
            msg += "\nRepeat has been disabled."
            await self.config.guild(ctx.guild).repeat.set(False)
        if autoplay is not True and disconnect is True:
            msg += "\nAuto-disconnecting at queue end has been disabled."
            await self.config.guild(ctx.guild).disconnect.set(False)

        await self.send_embed_msg(ctx, title="Setting Changed", description=msg)
        if self._player_check(ctx):
            await self.set_player_settings(ctx)

    @command_audioset_autoplay.command(name="playlist", usage="<playlist_name_OR_id> [args]")
    async def command_audioset_autoplay_playlist(self, ctx: commands.Context, playlist_matches: PlaylistConverter, *, scope_data: ScopeParser = None):
        """Set a playlist to auto-play songs from.

        **Usage**:
        \u200b \u200b \u200b \u200b `;audioset autoplay playlist_name_OR_id [args]`

        **Args**:
        \u200b \u200b \u200b \u200b The following are all optional:
        \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b --scope <scope>
        \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b --author [user]
        \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b --guild [guild] **Only the bot owner can use this**

        **Scope** is one of the following:
            \u200bGlobal
        \u200b \u200b \u200b \u200b Guild
        \u200b \u200b \u200b \u200b User

        **Author** can be one of the following:
        \u200b \u200b \u200b \u200b User ID
        \u200b \u200b \u200b \u200b User Mention
        \u200b \u200b \u200b \u200b User Name#123

        **Guild** can be one of the following:
        \u200b \u200b \u200b \u200b Guild ID
        \u200b \u200b \u200b \u200b Exact guild name

        Example use:
        \u200b \u200b \u200b \u200b `;audioset autoplay MyGuildPlaylist`
        \u200b \u200b \u200b \u200b `;audioset autoplay MyGlobalPlaylist --scope Global`
        \u200b \u200b \u200b \u200b `;audioset autoplay PersonalPlaylist --scope User --author Draper`

        """
        if self.playlist_api is None:
            return await self.send_embed_msg(
                ctx,
                title="Playlists Are Not Available",
                description="The playlist section of Audio is currently unavailable",
                footer="Check your logs." if await self.bot.is_owner(ctx.author) else discord.Embed.Empty,
            )

        if scope_data is None:
            scope_data = [None, ctx.author, ctx.guild, False]

        scope, author, guild, specified_user = scope_data
        try:
            playlist, playlist_arg, scope = await self.get_playlist_match(ctx, playlist_matches, scope, author, guild, specified_user)
        except TooManyMatches as e:
            return await self.send_embed_msg(ctx, title=str(e))
        if playlist is None:
            return await self.send_embed_msg(ctx, title="No Playlist Found", description=("Could not match '{arg}' to a playlist").format(arg=playlist_arg))
        try:
            tracks = playlist.tracks
            if not tracks:
                return await self.send_embed_msg(ctx, title="No Tracks Found", description=("Playlist {name} has no tracks.").format(name=playlist.name))
            playlist_data = {"enabled": True, "id": playlist.id, "name": playlist.name, "scope": scope}
            await self.config.guild(ctx.guild).autoplaylist.set(playlist_data)
        except RuntimeError:
            return await self.send_embed_msg(
                ctx,
                title="No Playlist Found",
                description=("Playlist {id} does not exist in {scope} scope.").format(id=playlist_arg, scope=self.humanize_scope(scope, the=True)),
            )
        except MissingGuild:
            return await self.send_embed_msg(ctx, title="Missing Arguments", description="You need to specify the Guild ID for the guild to lookup.")
        else:
            return await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("Playlist {name} (`{id}`) [**{scope}**] will be used for autoplay.").format(
                    name=playlist.name,
                    id=playlist.id,
                    scope=self.humanize_scope(scope, ctx=guild if scope == PlaylistScope.GUILD.value else author),
                ),
            )

    @command_audioset_autoplay.command(name="reset")
    async def command_audioset_autoplay_reset(self, ctx: commands.Context):
        """Resets auto-play to the default playlist."""
        playlist_data = {"enabled": True, "id": 42069, "name": "Aikaterna's curated tracks", "scope": PlaylistScope.GLOBAL.value}

        await self.config.guild(ctx.guild).autoplaylist.set(playlist_data)
        return await self.send_embed_msg(ctx, title="Setting Changed", description="Set auto-play playlist to play recently played tracks.")

    @command_audioset.command(name="globaldailyqueue")
    @commands.is_owner()
    async def command_audioset_global_historical_queue(self, ctx: commands.Context) -> None:
        """Toggle global daily queues.

        Global daily queues creates a playlist for all tracks played
        today.

        """
        daily_playlists = self._daily_global_playlist_cache.setdefault(self.bot.user.id, await self.config.daily_playlists())
        await self.config.daily_playlists.set(not daily_playlists)
        self._daily_global_playlist_cache[self.bot.user.id] = not daily_playlists
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Global daily queues: {true_or_false}.").format(true_or_false="Disabled" if daily_playlists else ("Enabled")),
        )

    @command_audioset.command(name="dailyqueue")
    @commands.guild_only()
    @commands.admin()
    async def command_audioset_historical_queue(self, ctx: commands.Context) -> None:
        """Toggle daily queues.

        Daily queues creates a playlist for all tracks played today.

        """
        daily_playlists = self._daily_playlist_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).daily_playlists())
        await self.config.guild(ctx.guild).daily_playlists.set(not daily_playlists)
        self._daily_playlist_cache[ctx.guild.id] = not daily_playlists
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Daily queues: {true_or_false}.").format(true_or_false="Disabled" if daily_playlists else ("Enabled")),
        )

    @command_audioset.command(name="dc")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def command_audioset_dc(self, ctx: commands.Context) -> None:
        """Toggle the bot auto-disconnecting when done playing.

        This setting takes precedence over `;audioset
        emptydisconnect`.

        """
        disconnect = await self.config.guild(ctx.guild).disconnect()
        autoplay = await self.config.guild(ctx.guild).auto_play()
        msg = "" + ("Auto-disconnection at queue end: {true_or_false}.").format(true_or_false="Disabled" if disconnect else ("Enabled"))
        if disconnect is not True and autoplay is True:
            msg += "\nAuto-play has been disabled."
            await self.config.guild(ctx.guild).auto_play.set(False)

        await self.config.guild(ctx.guild).disconnect.set(not disconnect)

        await self.send_embed_msg(ctx, title="Setting Changed", description=msg)

    @command_audioset.command(name="dj")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def command_audioset_dj(self, ctx: commands.Context):
        """Toggle DJ mode.

        DJ mode allows users with the DJ role to use audio commands.

        """
        dj_role = self._dj_role_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).dj_role())
        dj_role = ctx.guild.get_role(dj_role)
        if dj_role is None:
            await self.send_embed_msg(ctx, title="Missing DJ Role", description="Please set a role to use with DJ mode. Enter the role name or ID now.")

            try:
                pred = MessagePredicate.valid_role(ctx)
                await self.bot.wait_for("message", timeout=15.0, check=pred)
                await ctx.invoke(self.command_audioset_role, role_name=pred.result)
            except TimeoutError:
                return await self.send_embed_msg(ctx, title="Response timed out, try again later.")
        dj_enabled = self._dj_status_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).dj_enabled())
        await self.config.guild(ctx.guild).dj_enabled.set(not dj_enabled)
        self._dj_status_cache[ctx.guild.id] = not dj_enabled
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("DJ role: {true_or_false}.").format(true_or_false="Disabled" if dj_enabled else ("Enabled")),
        )

    @command_audioset.command(name="emptydisconnect")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_emptydisconnect(self, ctx: commands.Context, seconds: int):
        """Auto-disconnect from channel when bot is alone in it for x seconds, 0
        to disable.

        `;audioset dc` takes precedence over this setting.

        """
        if seconds < 0:
            return await self.send_embed_msg(ctx, title="Invalid Time", description="Seconds can't be less than zero.")
        if 10 > seconds > 0:
            seconds = 10
        if seconds == 0:
            enabled = False
            await self.send_embed_msg(ctx, title="Setting Changed", description="Empty disconnect disabled.")
        else:
            enabled = True
            await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("Empty disconnect timer set to {num_seconds}.").format(num_seconds=self.get_time_string(seconds)),
            )

        await self.config.guild(ctx.guild).emptydc_timer.set(seconds)
        await self.config.guild(ctx.guild).emptydc_enabled.set(enabled)

    @command_audioset.command(name="emptypause")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_emptypause(self, ctx: commands.Context, seconds: int):
        """Auto-pause after x seconds when room is empty, 0 to disable."""
        if seconds < 0:
            return await self.send_embed_msg(ctx, title="Invalid Time", description="Seconds can't be less than zero.")
        if 10 > seconds > 0:
            seconds = 10
        if seconds == 0:
            enabled = False
            await self.send_embed_msg(ctx, title="Setting Changed", description="Empty pause disabled.")
        else:
            enabled = True
            await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("Empty pause timer set to {num_seconds}.").format(num_seconds=self.get_time_string(seconds)),
            )
        await self.config.guild(ctx.guild).emptypause_timer.set(seconds)
        await self.config.guild(ctx.guild).emptypause_enabled.set(enabled)

    @command_audioset.command(name="lyrics")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_lyrics(self, ctx: commands.Context) -> None:
        """Prioritise tracks with lyrics."""
        prefer_lyrics = await self.config.guild(ctx.guild).prefer_lyrics()
        await self.config.guild(ctx.guild).prefer_lyrics.set(not prefer_lyrics)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Prefer tracks with lyrics: {true_or_false}.").format(true_or_false="Disabled" if prefer_lyrics else ("Enabled")),
        )

    @command_audioset.command(name="jukebox")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_jukebox(self, ctx: commands.Context, price: int):
        """Set a price for queueing tracks for non-mods, 0 to disable."""
        if price < 0:
            return await self.send_embed_msg(ctx, title="Invalid Price", description="Price can't be less than zero.")
        if price == 0:
            jukebox = False
            await self.send_embed_msg(ctx, title="Setting Changed", description="Jukebox mode disabled.")
        else:
            jukebox = True
            await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("Track queueing command price set to {price} {currency}.").format(
                    price=humanize_number(price),
                    currency=await bank.get_currency_name(ctx.guild),
                ),
            )

        await self.config.guild(ctx.guild).jukebox_price.set(price)
        await self.config.guild(ctx.guild).jukebox.set(jukebox)

    @command_audioset.command(name="localpath")
    @commands.is_owner()
    async def command_audioset_localpath(self, ctx: commands.Context, *, local_path=None):
        if not local_path:
            await self.config.localpath.set(str(cog_data_path(raw_name="Audio")))
            self.local_folder_current_path = cog_data_path(raw_name="Audio")
            return await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("The localtracks path location has been reset to {localpath}").format(localpath=str(cog_data_path(raw_name="Audio").absolute())),
            )

        info = await ctx.maybe_send_embed("Redacted")

        start_adding_reactions(info, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(info, ctx.author)
        await self.bot.wait_for("reaction_add", check=pred)

        if not pred.result:
            with contextlib.suppress(discord.HTTPException):
                await info.delete()
            return
        temp = LocalPath(local_path, self.local_folder_current_path, forced=True)
        if not temp.exists() or not temp.is_dir():
            return await self.send_embed_msg(
                ctx,
                title="Invalid Path",
                description=("{local_path} does not seem like a valid path.").format(local_path=local_path),
            )

        if not temp.localtrack_folder.exists():
            warn_msg = (
                "`{localtracks}` does not exist. The path will still be saved, but please check the path and create a localtracks folder in `{localfolder}` before attempting to play local tracks."
            ).format(localfolder=temp.absolute(), localtracks=temp.localtrack_folder.absolute())
            await self.send_embed_msg(ctx, title="Invalid Environment", description=warn_msg)
        local_path = str(temp.localtrack_folder.absolute())
        await self.config.localpath.set(local_path)
        self.local_folder_current_path = temp.localtrack_folder.absolute()
        return await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("The localtracks path location has been set to {localpath}").format(localpath=local_path),
        )

    @command_audioset.command(name="maxlength")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_maxlength(self, ctx: commands.Context, seconds: Union[int, str]):
        """Max length of a track to queue in seconds, 0 to disable.

        Accepts seconds or a value formatted like 00:00:00 (`hh:mm:ss`)
        or 00:00 (`mm:ss`). Invalid input will turn the max length
        setting off.

        """
        if not isinstance(seconds, int):
            seconds = self.time_convert(seconds)
        if seconds < 0:
            return await self.send_embed_msg(ctx, title="Invalid length", description="Length can't be less than zero.")
        if seconds == 0:
            await self.send_embed_msg(ctx, title="Setting Changed", description="Track max length disabled.")
        else:
            await self.send_embed_msg(
                ctx,
                title="Setting Changed",
                description=("Track max length set to {seconds}.").format(seconds=self.get_time_string(seconds)),
            )
        await self.config.guild(ctx.guild).maxlength.set(seconds)

    @command_audioset.command(name="notify")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def command_audioset_notify(self, ctx: commands.Context) -> None:
        """Toggle track announcement and other bot messages."""
        notify = await self.config.guild(ctx.guild).notify()
        await self.config.guild(ctx.guild).notify.set(not notify)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Notify mode: {true_or_false}.").format(true_or_false="Disabled" if notify else ("Enabled")),
        )

    @command_audioset.command(name="autodeafen")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def command_audioset_auto_deafen(self, ctx: commands.Context) -> None:
        """Toggle whether the bot will be auto deafened upon joining the voice
        channel.
        """
        auto_deafen = await self.config.guild(ctx.guild).auto_deafen()
        await self.config.guild(ctx.guild).auto_deafen.set(not auto_deafen)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Auto Deafen: {true_or_false}.").format(true_or_false="Disabled" if auto_deafen else ("Enabled")),
        )

    @command_audioset.command(name="restrict")
    @commands.is_owner()
    @commands.guild_only()
    async def command_audioset_restrict(self, ctx: commands.Context) -> None:
        """Toggle the domain restriction on Audio.

        When toggled off, users will be able to play songs from non-
        commercial websites and links. When toggled on, users are
        restricted to YouTube, SoundCloud, Vimeo, Twitch, and Bandcamp
        links.

        """
        restrict = await self.config.restrict()
        await self.config.restrict.set(not restrict)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Commercial links only: {true_or_false}.").format(true_or_false="Disabled" if restrict else ("Enabled")),
        )

    @command_audioset.command(name="role")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def command_audioset_role(self, ctx: commands.Context, *, role_name: discord.Role) -> None:
        """Set the role to use for DJ mode."""
        await self.config.guild(ctx.guild).dj_role.set(role_name.id)
        self._dj_role_cache[ctx.guild.id] = role_name.id
        dj_role = self._dj_role_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).dj_role())
        dj_role_obj = ctx.guild.get_role(dj_role)
        await self.send_embed_msg(ctx, title="Settings Changed", description=("DJ role set to: {role.name}.").format(role=dj_role_obj))

    @command_audioset.command(name="settings", aliases=["info"])
    @commands.guild_only()
    async def command_audioset_settings(self, ctx: commands.Context) -> None:
        """Show the current settings."""
        is_owner = await self.bot.is_owner(ctx.author)
        global_data = await self.config.all()
        data = await self.config.guild(ctx.guild).all()

        auto_deafen = "Enabled" if data["auto_deafen"] else ("Disabled")
        dj_role_obj = ctx.guild.get_role(data["dj_role"])
        dj_enabled = data["dj_enabled"]
        emptydc_enabled = data["emptydc_enabled"]
        emptydc_timer = data["emptydc_timer"]
        emptypause_enabled = data["emptypause_enabled"]
        emptypause_timer = data["emptypause_timer"]
        jukebox = data["jukebox"]
        jukebox_price = data["jukebox_price"]
        thumbnail = data["thumbnail"]
        dc = data["disconnect"]
        autoplay = data["auto_play"]
        maxlength = data["maxlength"]
        maxvolume = data["max_volume"]
        vote_percent = data["vote_percent"]
        current_level = CacheLevel(global_data["cache_level"])
        song_repeat = "Enabled" if data["repeat"] else ("Disabled")
        song_shuffle = "Enabled" if data["shuffle"] else ("Disabled")
        bumpped_shuffle = "Enabled" if data["shuffle_bumped"] else ("Disabled")
        song_notify = "Enabled" if data["notify"] else ("Disabled")
        song_status = "Enabled" if global_data["status"] else ("Disabled")
        persist_queue = "Enabled" if data["persist_queue"] else ("Disabled")

        countrycode = data["country_code"]

        spotify_cache = CacheLevel.set_spotify()
        youtube_cache = CacheLevel.set_youtube()
        lavalink_cache = CacheLevel.set_lavalink()
        has_spotify_cache = current_level.is_superset(spotify_cache)
        has_youtube_cache = current_level.is_superset(youtube_cache)
        has_lavalink_cache = current_level.is_superset(lavalink_cache)
        cache_enabled = CacheLevel.set_lavalink().is_subset(current_level)
        autoplaylist = data["autoplaylist"]
        vote_enabled = data["vote_enabled"]
        msg = "----" + "Server Settings" + "----        \n"
        msg += ("Auto-deafen:      [{auto_deafen}]\n").format(auto_deafen=auto_deafen)
        msg += ("Auto-disconnect:  [{dc}]\n").format(dc="Enabled" if dc else ("Disabled"))
        msg += ("Auto-play:        [{autoplay}]\n").format(autoplay="Enabled" if autoplay else ("Disabled"))
        if emptydc_enabled:
            msg += ("Disconnect timer: [{num_seconds}]\n").format(num_seconds=self.get_time_string(emptydc_timer))
        if emptypause_enabled:
            msg += ("Auto Pause timer: [{num_seconds}]\n").format(num_seconds=self.get_time_string(emptypause_timer))
        if dj_enabled and dj_role_obj:
            msg += ("DJ Role:          [{role.name}]\n").format(role=dj_role_obj)
        if jukebox:
            msg += ("Jukebox:          [{jukebox_name}]\n").format(jukebox_name=jukebox)
            msg += ("Command price:    [{jukebox_price}]\n").format(jukebox_price=humanize_number(jukebox_price))
        if maxlength > 0:
            msg += ("Max track length: [{tracklength}]\n").format(tracklength=self.get_time_string(maxlength))
        msg += (
            "Max volume:       [{max_volume}%]\nPersist queue:    [{persist_queue}]\nRepeat:           [{repeat}]\nShuffle:          [{shuffle}]\nShuffle bumped:   [{bumpped_shuffle}]\nSong notify msgs: [{notify}]\nSongs as status:  [{status}]\nSpotify search:   [{countrycode}]\n"
        ).format(
            max_volume=maxvolume,
            countrycode=countrycode,
            persist_queue=persist_queue,
            repeat=song_repeat,
            shuffle=song_shuffle,
            notify=song_notify,
            status=song_status,
            bumpped_shuffle=bumpped_shuffle,
        )
        if thumbnail:
            msg += f'Thumbnails:       [{"Enabled" if thumbnail else "Disabled"}]\n'
        if vote_percent > 0:
            msg += ("Vote skip:        [{vote_enabled}]\nSkip percentage:  [{vote_percent}%]\n").format(
                vote_percent=vote_percent,
                vote_enabled="Enabled" if vote_enabled else ("Disabled"),
            )
        if "enabled" not in autoplaylist:
            autoplaylist["enabled"] = False
        if autoplay or autoplaylist["enabled"]:
            if autoplaylist["enabled"]:
                pname = autoplaylist["name"]
                pid = autoplaylist["id"]
                pscope = autoplaylist["scope"]
                if pscope == PlaylistScope.GUILD.value:
                    pscope = "Server"
                elif pscope == PlaylistScope.USER.value:
                    pscope = "User"
                else:
                    pscope = "Global"
            elif cache_enabled:
                pname = "Cached"
                pid = "Cached"
                pscope = "Cached"
            else:
                pname = "US Top 100"
                pid = "US Top 100"
                pscope = "US Top 100"
            msg += (
                "\n---"
                + "Auto-play Settings"
                + "---        \n"
                + "Playlist name:    [{pname}]\n"
                + "Playlist ID:      [{pid}]\n"
                + "Playlist scope:   [{pscope}]\n"
            ).format(pname=pname, pid=pid, pscope=pscope)

        if is_owner:
            msg += (
                "\n---"
                + "Cache Settings"
                + "---        \n"
                + "Max age:                [{max_age}]\n"
                + "Local Spotify cache:    [{spotify_status}]\n"
                + "Local Youtube cache:    [{youtube_status}]\n"
                + "Local Lavalink cache:   [{lavalink_status}]\n"
            ).format(
                max_age=f"{str(await self.config.cache_age())} days",
                spotify_status="Enabled" if has_spotify_cache else ("Disabled"),
                youtube_status="Enabled" if has_youtube_cache else ("Disabled"),
                lavalink_status="Enabled" if has_lavalink_cache else ("Disabled"),
            )

        msg += ("\n---" + "User Settings" + "---        \n" + "Spotify search:   [{country_code}]\n").format(
            country_code=await self.config.user(ctx.author).country_code(),
        )

        msg += (
            "\n---"
            + "Lavalink Settings"
            + "---        \n"
            + "Cog version:            [{version}]\n"
            + "Melanie-Lavalink:           [{lavalink_version}]\n"
            + "External server:        [{use_external_lavalink}]\n"
        ).format(
            version=__version__,
            lavalink_version=lavalink.__version__,
            use_external_lavalink="Enabled" if global_data["use_external_lavalink"] else ("Disabled"),
        )
        if is_owner and not global_data["use_external_lavalink"] and self.player_manager.ll_build:
            msg += (
                "Lavalink build:         [{llbuild}]\nLavalink branch:        [{llbranch}]\nRelease date:           [{build_time}]\nLavaplayer version:     [{lavaplayer}]\nJava version:           [{jvm}]\nJava Executable:        [{jv_exec}]\n"
            ).format(
                build_time=self.player_manager.build_time,
                llbuild=self.player_manager.ll_build,
                llbranch=self.player_manager.ll_branch,
                lavaplayer=self.player_manager.lavaplayer,
                jvm=self.player_manager.jvm,
                jv_exec=self.player_manager.path,
            )
        if is_owner:
            msg += ("Localtracks path:       [{localpath}]\n").format(**global_data)

        await self.send_embed_msg(ctx, description=box(msg, lang="ini"))

    @command_audioset.command(name="logs")
    @commands.is_owner()
    @has_internal_server()
    @commands.guild_only()
    async def command_audioset_logs(self, ctx: commands.Context):
        """Sends the Lavalink server logs to your DMs."""
        datapath = cog_data_path(raw_name="Audio")
        logs = datapath / "logs" / "spring.log"
        zip_name = None
        try:
            try:
                if not (logs.exists() and logs.is_file()):
                    return await ctx.send("No logs found in your data folder.")
            except OSError:
                return await ctx.send("No logs found in your data folder.")

            def check(path):
                return os.path.getsize(str(path)) > (8388608 - 1000)

            if check(logs):
                zip_name = logs.with_suffix(".tar.gz")
                zip_name.unlink(missing_ok=True)
                with tarfile.open(zip_name, "w:gz") as tar:
                    tar.add(str(logs), arcname="spring.log", recursive=False)
                if check(zip_name):
                    await ctx.send(("Logs are too large, you can find them in {path}").format(path=zip_name.absolute()))
                    zip_name = None
                else:
                    await ctx.author.send(file=discord.File(str(zip_name)))
            else:
                await ctx.author.send(file=discord.File(str(logs)))
        except discord.HTTPException:
            await ctx.send("I need to be able to DM you to send you the logs.")
        finally:
            if zip_name is not None:
                zip_name.unlink(missing_ok=True)

    @command_audioset.command(name="status")
    @commands.is_owner()
    @commands.guild_only()
    async def command_audioset_status(self, ctx: commands.Context) -> None:
        """Enable/disable tracks' titles as status."""
        status = await self.config.status()
        await self.config.status.set(not status)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Song titles as status: {true_or_false}.").format(true_or_false="Disabled" if status else ("Enabled")),
        )

    @command_audioset.command(name="thumbnail")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_thumbnail(self, ctx: commands.Context) -> None:
        """Toggle displaying a thumbnail on audio messages."""
        thumbnail = await self.config.guild(ctx.guild).thumbnail()
        await self.config.guild(ctx.guild).thumbnail.set(not thumbnail)
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Thumbnail display: {true_or_false}.").format(true_or_false="Disabled" if thumbnail else ("Enabled")),
        )

    @command_audioset.command(name="vote")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_vote(self, ctx: commands.Context, percent: int):
        """Percentage needed for non-mods to skip tracks, 0 to disable."""
        if percent < 0:
            return await self.send_embed_msg(ctx, title="Invalid Time", description="Seconds can't be less than zero.")
        elif percent > 100:
            percent = 100
        if percent == 0:
            enabled = False
            await self.send_embed_msg(ctx, title="Setting Changed", description="Voting disabled. All users can use queue management commands.")
        else:
            enabled = True
            await self.send_embed_msg(ctx, title="Setting Changed", description=("Vote percentage set to {percent}%.").format(percent=percent))

        await self.config.guild(ctx.guild).vote_percent.set(percent)
        await self.config.guild(ctx.guild).vote_enabled.set(enabled)

    @command_audioset.command(name="youtubeapi")
    @commands.is_owner()
    async def command_audioset_youtubeapi(self, ctx: commands.Context) -> None:
        """Instructions to set the YouTube API key."""
        message = ("1. Go to Google Developers Console and log in with your Google account.\\n").format()

        await ctx.maybe_send_embed(message)

    @command_audioset.command(name="spotifyapi")
    @commands.is_owner()
    async def command_audioset_spotifyapi(self, ctx: commands.Context) -> None:
        """Instructions to set the Spotify API tokens."""
        message = _(
            '1. Go to Spotify developers and log in with your Spotify account.\n(https://developer.spotify.com/dashboard/applications)\n2. Click "Create An App".\n3. Fill out the form provided with your app name, etc.\n4. When asked if you\'re developing commercial integration select "No".\n5. Accept the terms and conditions.\n6. Copy your client ID and your client secret into:\n`{prefix}set api spotify client_id <your_client_id_here> client_secret <your_client_secret_here>`',
        ).format(prefix=ctx.prefix)
        await ctx.maybe_send_embed(message)

    @command_audioset.command(name="countrycode")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def command_audioset_countrycode(self, ctx: commands.Context, country: str):
        """Set the country code for Spotify searches."""
        if len(country) != 2:
            return await self.send_embed_msg(
                ctx,
                title="Invalid Country Code",
                description="Please use an official [ISO 3166-1 alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) code.",
            )
        country = country.upper()
        await self.send_embed_msg(ctx, title="Setting Changed", description=("Country Code set to {country}.").format(country=country))

        await self.config.guild(ctx.guild).country_code.set(country)

    @command_audioset.command(name="mycountrycode")
    @commands.guild_only()
    async def command_audioset_countrycode_user(self, ctx: commands.Context, country: str):
        """Set the country code for Spotify searches."""
        if len(country) != 2:
            return await self.send_embed_msg(
                ctx,
                title="Invalid Country Code",
                description="Please use an official [ISO 3166-1 alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) code.",
            )
        country = country.upper()
        await self.send_embed_msg(ctx, title="Setting Changed", description=("Country Code set to {country}.").format(country=country))

        await self.config.user(ctx.author).country_code.set(country)

    @command_audioset.command(name="cache")
    @commands.is_owner()
    async def command_audioset_cache(self, ctx: commands.Context, *, level: int = None):
        """Sets the caching level.

        Level can be one of the following:

        0: Disables all caching
        1: Enables Spotify Cache
        2: Enables YouTube Cache
        3: Enables Lavalink Cache
        5: Enables all Caches

        If you wish to disable a specific cache use a negative number.

        """
        current_level = CacheLevel(await self.config.cache_level())
        spotify_cache = CacheLevel.set_spotify()
        youtube_cache = CacheLevel.set_youtube()
        lavalink_cache = CacheLevel.set_lavalink()
        has_spotify_cache = current_level.is_superset(spotify_cache)
        has_youtube_cache = current_level.is_superset(youtube_cache)
        has_lavalink_cache = current_level.is_superset(lavalink_cache)

        if level is None:
            msg = (
                "Max age:          [{max_age}]\n"
                + "Spotify cache:    [{spotify_status}]\n"
                + "Youtube cache:    [{youtube_status}]\n"
                + "Lavalink cache:   [{lavalink_status}]\n"
            ).format(
                max_age=f"{str(await self.config.cache_age())} days",
                spotify_status="Enabled" if has_spotify_cache else ("Disabled"),
                youtube_status="Enabled" if has_youtube_cache else ("Disabled"),
                lavalink_status="Enabled" if has_lavalink_cache else ("Disabled"),
            )

            await self.send_embed_msg(ctx, title="Cache Settings", description=box(msg, lang="ini"))
            return await ctx.send_help()
        if level not in [5, 3, 2, 1, 0, -1, -2, -3]:
            return await ctx.send_help()

        removing = level < 0

        if level == 5:
            newcache = CacheLevel.all()
        elif level == 0:
            newcache = CacheLevel.none()
        elif level in {-3, 3}:
            newcache = current_level - lavalink_cache if removing else current_level + lavalink_cache
        elif level in {-2, 2}:
            newcache = current_level - youtube_cache if removing else current_level + youtube_cache
        elif level in {-1, 1}:
            newcache = current_level - spotify_cache if removing else current_level + spotify_cache
        else:
            return await ctx.send_help()

        has_spotify_cache = newcache.is_superset(spotify_cache)
        has_youtube_cache = newcache.is_superset(youtube_cache)
        has_lavalink_cache = newcache.is_superset(lavalink_cache)
        msg = (
            "Max age:          [{max_age}]\n"
            + "Spotify cache:    [{spotify_status}]\n"
            + "Youtube cache:    [{youtube_status}]\n"
            + "Lavalink cache:   [{lavalink_status}]\n"
        ).format(
            max_age=f"{str(await self.config.cache_age())} days",
            spotify_status="Enabled" if has_spotify_cache else ("Disabled"),
            youtube_status="Enabled" if has_youtube_cache else ("Disabled"),
            lavalink_status="Enabled" if has_lavalink_cache else ("Disabled"),
        )

        await self.send_embed_msg(ctx, title="Cache Settings", description=box(msg, lang="ini"))

        await self.config.cache_level.set(newcache.value)

    @command_audioset.command(name="cacheage")
    @commands.is_owner()
    async def command_audioset_cacheage(self, ctx: commands.Context, age: int) -> None:
        """Sets the cache max age.

        This commands allows you to set the max number of days before an
        entry in the cache becomes invalid.

        """
        msg = ""
        if age < 7:
            msg = ("Cache age cannot be less than 7 days. If you wish to disable it run {prefix}audioset cache.\n").format(prefix=ctx.prefix)
            age = 7
        msg += ("I've set the cache age to {age} days").format(age=age)
        await self.config.cache_age.set(age)
        await self.send_embed_msg(ctx, title="Setting Changed", description=msg)

    @command_audioset.command(name="persistqueue")
    @commands.admin()
    async def command_audioset_persist_queue(self, ctx: commands.Context) -> None:
        """Toggle persistent queues.

        Persistent queues allows the current queue to be restored when
        the queue closes.

        """
        persist_cache = self._persist_queue_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).persist_queue())
        await self.config.guild(ctx.guild).persist_queue.set(not persist_cache)
        self._persist_queue_cache[ctx.guild.id] = not persist_cache
        await self.send_embed_msg(
            ctx,
            title="Setting Changed",
            description=("Persisting queues: {true_or_false}.").format(true_or_false="Disabled" if persist_cache else ("Enabled")),
        )

    @command_audioset.command(name="restart")
    @commands.is_owner()
    async def command_audioset_restart(self, ctx: commands.Context) -> None:
        """Restarts the lavalink connection."""
        async with ctx.typing():
            await lavalink.close(self.bot)
            if self.player_manager is not None:
                await self.player_manager.shutdown()

            self.lavalink_restart_connect()

            await self.send_embed_msg(ctx, title="Restarting Lavalink", description="It can take a couple of minutes for Lavalink to fully start up.")

    @command_audioset.command(usage="<maximum volume>", name="maxvolume")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def command_audioset_maxvolume(self, ctx: commands.Context, max_volume: int):
        """Set the maximum volume allowed in this server."""
        if max_volume < 1:
            return await self.send_embed_msg(ctx, title="Error", description="Music without sound isn't music at all. Try setting the volume higher then 0%.")
        elif max_volume > 150:
            max_volume = 150
            await self.send_embed_msg(ctx, title="Setting changed", description="The maximum volume has been limited to 150%, be easy on your ears.")
        else:
            await self.send_embed_msg(
                ctx,
                title="Setting changed",
                description=("The maximum volume has been limited to {max_volume}%.").format(max_volume=max_volume),
            )
        current_volume = await self.config.guild(ctx.guild).volume()
        if current_volume > max_volume:
            await self.config.guild(ctx.guild).volume.set(max_volume)
            if self._player_check(ctx):
                player = lavalink.get_player(ctx.guild.id)
                await player.set_volume(max_volume)
                player.store("notify_channel", ctx.channel.id)

        await self.config.guild(ctx.guild).max_volume.set(max_volume)
