from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import MutableMapping
from typing import Optional

import discord
import lavalink
from boltons.urlutils import URL, find_all_links
from discord.embeds import EmptyEmbed
from melaniebot.cogs.alias.alias import current_alias
from melaniebot.core import commands
from melaniebot.core.commands import UserInputOptional
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.menus import (
    DEFAULT_CONTROLS,
    close_menu,
    menu,
    next_page,
    prev_page,
)

from audio.audio_dataclasses import _PARTIALLY_SUPPORTED_MUSIC_EXT, Query
from audio.audio_logging import IS_DEBUG
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass
from audio.errors import QueryUnauthorized, TrackEnqueueError
from melanie import log


def _(x):
    return x


class PlayerCommands(MixinMeta, metaclass=CompositeMetaClass):
    @commands.command(name="play", aliases=["start", "p"])
    @commands.guild_only()
    async def command_play(self, ctx: commands.Context, *, query: Optional[str]):
        """Play the specified track or search for a close match."""
        if not query:
            message: discord.Message = ctx.message
            if not message.attachments:
                return await ctx.send_help()
            query = str(message.attachments[0].url)

        links: list[URL] = find_all_links(query)
        for l in links:
            if l.host and "spotify.link" in l.host:
                async with self.bot.htx.stream("GET", str(l), follow_redirects=False) as r:
                    if "location" in r.headers:
                        query = str(r.headers["location"])
                        break
        query = Query.process_input(query, self.local_folder_current_path)
        guild_data = await self.config.guild(ctx.guild).all()
        restrict = await self.config.restrict()
        if restrict and self.match_url(str(query)):
            valid_url = self.is_url_allowed(str(query))
            if not valid_url:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That URL is not allowed.")
        elif not await self.is_query_allowed(self.config, ctx, f"{query}", query_obj=query):
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That track is not allowed.")
        can_skip = await self._can_instaskip(ctx, ctx.author)
        if guild_data["dj_enabled"] and not can_skip:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="You need the DJ role to queue tracks.")
        if not self._player_check(ctx):
            if self.lavalink_connection_aborted:
                msg = "Connection to Lavalink has failed"
                desc = EmptyEmbed
                if await self.bot.is_owner(ctx.author):
                    desc = "Please check your console or logs for details."
                return await self.send_embed_msg(ctx, title=msg, description=desc)
            try:
                try:
                    if (
                        not self.can_join_and_speak(ctx.author.voice.channel)
                        or not ctx.author.voice.channel.permissions_for(ctx.me).move_members
                        and self.is_vc_full(ctx.author.voice.channel)
                    ):
                        return await self.send_embed_msg(
                            ctx,
                            title="Unable To Play Tracks",
                            description="I don't have permission to connect and speak in your channel.",
                        )
                    await lavalink.connect(ctx.author.voice.channel, deafen=await self.config.guild_from_id(ctx.guild.id).auto_deafen())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Issue playing {}", query)
                    if current_alias.get():
                        return
                    raise
            except AttributeError:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Connect to a voice channel first.")
            except IndexError:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Connection to Lavalink has not yet been established.")
        player = lavalink.get_player(ctx.guild.id)
        player.store("notify_channel", ctx.channel.id)
        await self._eq_check(ctx, player)
        await self.set_player_settings(ctx)
        if (not ctx.author.voice or ctx.author.voice.channel != player.channel) and not can_skip:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="You must be in the voice channel to use the play command.")
        if not query.valid:
            return await self.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description=("No tracks found for `{query}`.").format(query=query.to_string_user()),
            )
        if len(player.queue) >= 10000:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Queue size limit reached.")

        if not await self.maybe_charge_requester(ctx, guild_data["jukebox_price"]):
            return
        if query.is_spotify:
            return await self._get_spotify_tracks(ctx, query)
        try:
            await self._enqueue_tracks(ctx, query)
        except QueryUnauthorized as err:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description=err.message)
        except Exception as e:
            self.update_player_lock(ctx, False)
            raise e

    @commands.command(name="bumpplay")
    @commands.guild_only()
    async def command_bumpplay(self, ctx: commands.Context, play_now: UserInputOptional[bool] = False, *, query: str):  # type: ignore
        """Force play a URL or search for a track."""
        query = Query.process_input(query, self.local_folder_current_path)
        if not query.single_track:
            return await self.send_embed_msg(ctx, title="Unable To Bump Track", description="Only single tracks work with bump play.")
        guild_data = await self.config.guild(ctx.guild).all()
        restrict = await self.config.restrict()
        if restrict and self.match_url(str(query)):
            valid_url = self.is_url_allowed(str(query))
            if not valid_url:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That URL is not allowed.")
        elif not await self.is_query_allowed(self.config, ctx, f"{query}", query_obj=query):
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That track is not allowed.")
        can_skip = await self._can_instaskip(ctx, ctx.author)
        if guild_data["dj_enabled"] and not can_skip:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="You need the DJ role to queue tracks.")
        if not self._player_check(ctx):
            if self.lavalink_connection_aborted:
                msg = "Connection to Lavalink has failed"
                desc = EmptyEmbed
                if await self.bot.is_owner(ctx.author):
                    desc = "Please check your console or logs for details."
                return await self.send_embed_msg(ctx, title=msg, description=desc)
            try:
                try:
                    if (
                        not self.can_join_and_speak(ctx.author.voice.channel)
                        or not ctx.author.voice.channel.permissions_for(ctx.me).move_members
                        and self.is_vc_full(ctx.author.voice.channel)
                    ):
                        return await self.send_embed_msg(
                            ctx,
                            title="Unable To Play Tracks",
                            description="I don't have permission to connect and speak in your channel.",
                        )
                    await lavalink.connect(ctx.author.voice.channel, deafen=await self.config.guild_from_id(ctx.guild.id).auto_deafen())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Issue playing {}", query)
                    if current_alias.get():
                        return
                    raise
            except AttributeError:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Connect to a voice channel first.")
            except IndexError:
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Connection to Lavalink has not yet been established.")
        player = lavalink.get_player(ctx.guild.id)
        player.store("notify_channel", ctx.channel.id)
        await self._eq_check(ctx, player)
        await self.set_player_settings(ctx)
        if (not ctx.author.voice or ctx.author.voice.channel != player.channel) and not can_skip:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="You must be in the voice channel to use the play command.")
        if not query.valid:
            return await self.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description=("No tracks found for `{query}`.").format(query=query.to_string_user()),
            )
        if len(player.queue) >= 10000:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Queue size limit reached.")

        if not await self.maybe_charge_requester(ctx, guild_data["jukebox_price"]):
            return
        try:
            if query.is_spotify:
                tracks = await self._get_spotify_tracks(ctx, query)
            else:
                tracks = await self._enqueue_tracks(ctx, query, enqueue=False)
        except QueryUnauthorized as err:
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description=err.message)
        except Exception as e:
            self.update_player_lock(ctx, False)
            raise e
        if isinstance(tracks, discord.Message):
            return
        elif not tracks:
            self.update_player_lock(ctx, False)
            title = "Unable To Play Tracks"
            desc = ("No tracks found for `{query}`.").format(query=query.to_string_user())
            embed = discord.Embed(title=title, description=desc)
            if await self.config.use_external_lavalink() and query.is_local:
                embed.description = "Local tracks will not work if the `Lavalink.jar` cannot see the track.\nThis may be due to permissions or because Lavalink.jar is being run in a different machine than the local tracks."
            elif query.is_local and query.suffix in _PARTIALLY_SUPPORTED_MUSIC_EXT:
                title = "Track is not playable."
                embed = discord.Embed(title=title)
                embed.description = ("**{suffix}** is not a fully supported format and some tracks may not play.").format(suffix=query.suffix)
            return await self.send_embed_msg(ctx, embed=embed)
        queue_dur = await self.track_remaining_duration(ctx)
        index = query.track_index
        seek = query.start_time or 0
        single_track = tracks if isinstance(tracks, lavalink.rest_api.Track) else tracks[index] if index else tracks[0]
        if seek and seek > 0:
            single_track.start_timestamp = seek * 1000
        query = Query.process_input(single_track, self.local_folder_current_path)
        if not await self.is_query_allowed(self.config, ctx, f"{single_track.title} {single_track.author} {single_track.uri} {str(query)}", query_obj=query):
            if IS_DEBUG:
                log.debug("Query is not allowed in %r ({})", ctx.guild.name, ctx.guild.id)
            self.update_player_lock(ctx, False)
            return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="This track is not allowed in this server.")
        elif guild_data["maxlength"] > 0:
            if self.is_track_length_allowed(single_track, guild_data["maxlength"]):
                single_track.requester = ctx.author
                single_track.extras.update({"enqueue_time": int(time.time()), "vc": player.channel.id, "requester": ctx.author.id})
                player.queue.insert(0, single_track)
                player.maybe_shuffle()
                self.bot.dispatch("red_audio_track_enqueue", player.guild, single_track, ctx.author)
            else:
                self.update_player_lock(ctx, False)
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="Track exceeds maximum length.")

        else:
            single_track.requester = ctx.author
            single_track.extras["bumped"] = True
            single_track.extras.update({"enqueue_time": int(time.time()), "vc": player.channel.id, "requester": ctx.author.id})
            player.queue.insert(0, single_track)
            player.maybe_shuffle()
            self.bot.dispatch("red_audio_track_enqueue", player.guild, single_track, ctx.author)
        description = await self.get_track_description(single_track, self.local_folder_current_path)
        footer = None
        if not play_now and not guild_data["shuffle"] and queue_dur > 0:
            footer = ("{time} until track playback: #1 in queue").format(time=self.format_time(queue_dur))
        await self.send_embed_msg(ctx, title="Track Enqueued", description=description, footer=footer)

        if not player.current:
            await player.play()
        elif play_now:
            await player.skip()

        self.update_player_lock(ctx, False)

    @commands.command(name="search")
    @commands.guild_only()
    async def command_search(self, ctx: commands.Context, *, query: str):
        """Pick a track with a search.

        Use `;search list <search term>` to queue all tracks found on
        YouTube. Use `;search sc <search term>` to search on
        SoundCloud instead of YouTube.

        """
        if not isinstance(query, (str, list, Query)):
            msg = f"Expected 'query' to be a string, list or Query object but received: {type(query)} - this is an unexpected argument type, please report it."
            raise RuntimeError(msg)

        async def _search_menu(ctx: commands.Context, pages: list, controls: MutableMapping, message: discord.Message, page: int, timeout: float, emoji: str):
            if message:
                await self._search_button_action(ctx, tracks, emoji, page)
                with contextlib.suppress(discord.HTTPException):
                    await message.delete()
                return None

        search_controls = {
            "\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}": _search_menu,
            "\N{DIGIT TWO}\N{COMBINING ENCLOSING KEYCAP}": _search_menu,
            "\N{DIGIT THREE}\N{COMBINING ENCLOSING KEYCAP}": _search_menu,
            "\N{DIGIT FOUR}\N{COMBINING ENCLOSING KEYCAP}": _search_menu,
            "\N{DIGIT FIVE}\N{COMBINING ENCLOSING KEYCAP}": _search_menu,
            "\N{LEFTWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": prev_page,
            "\N{CROSS MARK}": close_menu,
            "\N{BLACK RIGHTWARDS ARROW}\N{VARIATION SELECTOR-16}": next_page,
        }

        if not self._player_check(ctx):
            if self.lavalink_connection_aborted:
                msg = "Connection to Lavalink has failed"
                desc = EmptyEmbed
                if await self.bot.is_owner(ctx.author):
                    desc = "Please check your console or logs for details."
                return await self.send_embed_msg(ctx, title=msg, description=desc)
            try:
                if (
                    not self.can_join_and_speak(ctx.author.voice.channel)
                    or not ctx.author.voice.channel.permissions_for(ctx.me).move_members
                    and self.is_vc_full(ctx.author.voice.channel)
                ):
                    return await self.send_embed_msg(
                        ctx,
                        title="Unable To Search For Tracks",
                        description="I don't have permission to connect and speak in your channel.",
                    )
                await lavalink.connect(ctx.author.voice.channel, deafen=await self.config.guild_from_id(ctx.guild.id).auto_deafen())
            except AttributeError:
                return await self.send_embed_msg(ctx, title="Unable To Search For Tracks", description="Connect to a voice channel first.")
            except IndexError:
                return await self.send_embed_msg(ctx, title="Unable To Search For Tracks", description="Connection to Lavalink has not yet been established.")
        player = lavalink.get_player(ctx.guild.id)
        guild_data = await self.config.guild(ctx.guild).all()
        player.store("notify_channel", ctx.channel.id)
        can_skip = await self._can_instaskip(ctx, ctx.author)
        if (not ctx.author.voice or ctx.author.voice.channel != player.channel) and not can_skip:
            return await self.send_embed_msg(ctx, title="Unable To Search For Tracks", description="You must be in the voice channel to enqueue tracks.")
        await self._eq_check(ctx, player)
        await self.set_player_settings(ctx)

        before_queue_length = len(player.queue)

        if not isinstance(query, list):
            query = Query.process_input(query, self.local_folder_current_path)
            restrict = await self.config.restrict()
            if restrict and self.match_url(str(query)):
                valid_url = self.is_url_allowed(str(query))
                if not valid_url:
                    return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That URL is not allowed.")
            if not await self.is_query_allowed(self.config, ctx, f"{query}", query_obj=query):
                return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="That track is not allowed.")
            if query.invoked_from in ["search list", "local folder"]:
                if query.invoked_from == "search list" and not query.is_local:
                    try:
                        result, called_api = await self.api_interface.fetch_track(ctx, player, query)
                    except TrackEnqueueError:
                        self.update_player_lock(ctx, False)
                        return await self.send_embed_msg(
                            ctx,
                            title="Unable to Get Track",
                            description="I'm unable to get a track from Lavalink at the moment, try again in a few minutes.",
                        )
                    except Exception as e:
                        self.update_player_lock(ctx, False)
                        raise e

                    tracks = result.tracks
                else:
                    try:
                        query.search_subfolders = True
                        tracks = await self.get_localtrack_folder_tracks(ctx, player, query)
                    except TrackEnqueueError:
                        self.update_player_lock(ctx, False)
                        return await self.send_embed_msg(
                            ctx,
                            title="Unable to Get Track",
                            description="I'm unable to get a track from Lavalink at the moment, try again in a few minutes.",
                        )
                    except Exception as e:
                        self.update_player_lock(ctx, False)
                        raise e
                if not tracks:
                    embed = discord.Embed(title="Nothing found.")
                    if await self.config.use_external_lavalink() and query.is_local:
                        embed.description = "Local tracks will not work if the `Lavalink.jar` cannot see the track.\nThis may be due to permissions or because Lavalink.jar is being run in a different machine than the local tracks."
                    elif query.is_local and query.suffix in _PARTIALLY_SUPPORTED_MUSIC_EXT:
                        embed = discord.Embed(title="Track is not playable.")
                        embed.description = ("**{suffix}** is not a fully supported format and some tracks may not play.").format(suffix=query.suffix)
                    return await self.send_embed_msg(ctx, embed=embed)
                queue_dur = await self.queue_duration(ctx)
                queue_total_duration = self.format_time(queue_dur)
                if guild_data["dj_enabled"] and not can_skip:
                    return await self.send_embed_msg(ctx, title="Unable To Play Tracks", description="You need the DJ role to queue tracks.")
                track_len = 0
                empty_queue = not player.queue
                async for track in AsyncIter(tracks):
                    if len(player.queue) >= 10000:
                        continue
                    query = Query.process_input(track, self.local_folder_current_path)
                    if not await self.is_query_allowed(self.config, ctx, f"{track.title} {track.author} {track.uri} {str(query)}", query_obj=query):
                        if IS_DEBUG:
                            log.debug("Query is not allowed in %r ({})", ctx.guild.name, ctx.guild.id)
                        continue
                    elif guild_data["maxlength"] > 0:
                        if self.is_track_length_allowed(track, guild_data["maxlength"]):
                            track_len += 1
                            track.extras.update({"enqueue_time": int(time.time()), "vc": player.channel.id, "requester": ctx.author.id})
                            player.add(ctx.author, track)
                            self.bot.dispatch("red_audio_track_enqueue", player.guild, track, ctx.author)
                    else:
                        track_len += 1
                        track.extras.update({"enqueue_time": int(time.time()), "vc": player.channel.id, "requester": ctx.author.id})
                        player.add(ctx.author, track)
                        self.bot.dispatch("red_audio_track_enqueue", player.guild, track, ctx.author)
                    if not player.current:
                        await player.play()
                player.maybe_shuffle(0 if empty_queue else 1)
                maxlength_msg = " {bad_tracks} tracks cannot be queued.".format(bad_tracks=len(tracks) - track_len) if len(tracks) > track_len else ""
                songembed = discord.Embed(title=("Queued {num} track(s).{maxlength_msg}").format(num=track_len, maxlength_msg=maxlength_msg))
                if not guild_data["shuffle"] and queue_dur > 0:
                    footer = "folder" if query.is_local and query.is_album else ("search")
                    songembed.set_footer(
                        text=("{time} until start of {type} playback: starts at #{position} in queue").format(
                            time=queue_total_duration,
                            position=before_queue_length + 1,
                            type=footer,
                        ),
                    )
                return await self.send_embed_msg(ctx, embed=songembed)
            elif query.is_local and query.single_track:
                tracks = await self.get_localtrack_folder_list(ctx, query)
            elif query.is_local and query.is_album:
                if ctx.invoked_with == "folder":
                    return await self._local_play_all(ctx, query, from_search=True)
                else:
                    tracks = await self.get_localtrack_folder_list(ctx, query)
            else:
                try:
                    result, called_api = await self.api_interface.fetch_track(ctx, player, query)
                except TrackEnqueueError:
                    self.update_player_lock(ctx, False)
                    return await self.send_embed_msg(
                        ctx,
                        title="Unable to Get Track",
                        description="I'm unable to get a track from Lavalink at the moment, try again in a few minutes.",
                    )
                except Exception as e:
                    self.update_player_lock(ctx, False)
                    raise e
                tracks = result.tracks
            if not tracks:
                embed = discord.Embed(title="Nothing found.")
                if await self.config.use_external_lavalink() and query.is_local:
                    embed.description = "Local tracks will not work if the `Lavalink.jar` cannot see the track.\nThis may be due to permissions or because Lavalink.jar is being run in a different machine than the local tracks."
                elif query.is_local and query.suffix in _PARTIALLY_SUPPORTED_MUSIC_EXT:
                    embed = discord.Embed(title="Track is not playable.")
                    embed.description = ("**{suffix}** is not a fully supported format and some tracks may not play.").format(suffix=query.suffix)
                return await self.send_embed_msg(ctx, embed=embed)
        else:
            tracks = query

        dj_enabled = self._dj_status_cache.setdefault(ctx.guild.id, await self.config.guild(ctx.guild).dj_enabled())

        len_search_pages = math.ceil(len(tracks) / 5)
        search_page_list = []
        async for page_num in AsyncIter(range(1, len_search_pages + 1)):
            embed = await self._build_search_page(ctx, tracks, page_num)
            search_page_list.append(embed)

        if dj_enabled and not can_skip:
            return await menu(ctx, search_page_list, DEFAULT_CONTROLS)

        await menu(ctx, search_page_list, search_controls)
