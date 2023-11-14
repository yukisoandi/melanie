from __future__ import annotations

import math

import discord
import lavalink
from fuzzywuzzy import process
from melaniebot.core import commands
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import humanize_number

from audio.audio_dataclasses import LocalPath, Query
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass


def _(x):
    return x


class QueueUtilities(MixinMeta, metaclass=CompositeMetaClass):
    async def _build_queue_page(self, ctx: commands.Context, queue: list, player: lavalink.player_manager.Player, page_num: int) -> discord.Embed:
        shuffle = await self.config.guild(ctx.guild).shuffle()
        repeat = await self.config.guild(ctx.guild).repeat()
        autoplay = await self.config.guild(ctx.guild).auto_play()

        queue_num_pages = math.ceil(len(queue) / 10)
        queue_idx_start = (page_num - 1) * 10
        queue_idx_end = queue_idx_start + 10
        queue_list = "__Too many songs in the queue, only showing the first 500__.\n\n" if len(player.queue) > 500 else ""

        arrow = await self.draw_time(ctx)
        pos = self.format_time(player.position)

        dur = "LIVE" if player.current.is_stream else self.format_time(player.current.length)

        query = Query.process_input(player.current, self.local_folder_current_path)
        current_track_description = await self.get_track_description(player.current, self.local_folder_current_path)
        if query.is_stream:
            queue_list += "**Currently livestreaming:**\n"
        else:
            queue_list += "Playing: "
        queue_list += f"{current_track_description}\n"
        queue_list += ("Requested by: **{user}**").format(user=player.current.requester)
        queue_list += f"\n\n{arrow}`{pos}`/`{dur}`\n\n"
        async for i, track in AsyncIter(queue[queue_idx_start:queue_idx_end]).enumerate(start=queue_idx_start):
            req_user = track.requester
            track_idx = i + 1
            track_description = await self.get_track_description(track, self.local_folder_current_path, shorten=True)
            queue_list += f"`{track_idx}.` {track_description}, "
            queue_list += ("requested by **{user}**\n").format(user=req_user)

        embed = discord.Embed(colour=await ctx.embed_colour(), title=("Queue for __{guild_name}__").format(guild_name=ctx.guild.name), description=queue_list)

        if await self.config.guild(ctx.guild).thumbnail() and player.current.thumbnail:
            embed.set_thumbnail(url=player.current.thumbnail)
        queue_dur = await self.queue_duration(ctx)
        queue_total_duration = self.format_time(queue_dur)
        text = ("Page {page_num}/{total_pages} | {num_tracks} tracks, {num_remaining} remaining\n").format(
            page_num=humanize_number(page_num),
            total_pages=humanize_number(queue_num_pages),
            num_tracks=len(player.queue),
            num_remaining=queue_total_duration,
        )
        text += "Auto-Play" + ": " + ("\N{WHITE HEAVY CHECK MARK}" if autoplay else "\N{CROSS MARK}")
        text += (" | " if text else "") + "Shuffle" + ": " + ("\N{WHITE HEAVY CHECK MARK}" if shuffle else "\N{CROSS MARK}")
        text += (" | " if text else "") + "Repeat" + ": " + ("\N{WHITE HEAVY CHECK MARK}" if repeat else "\N{CROSS MARK}")
        embed.set_footer(text=text)
        return embed

    async def _build_queue_search_list(self, queue_list: list[lavalink.Track], search_words: str) -> list[tuple[int, str]]:
        track_list = []
        async for queue_idx, track in AsyncIter(queue_list).enumerate(start=1):
            if not self.match_url(track.uri):
                query = Query.process_input(track, self.local_folder_current_path)
                if query.is_local and query.local_track_path is not None and track.title == "Unknown title":
                    track_title = query.local_track_path.to_string_user()
                else:
                    track_title = f"{track.author} - {track.title}"
            else:
                track_title = track.title

            song_info = {str(queue_idx): track_title}
            track_list.append(song_info)
        search_results = process.extract(search_words, track_list, limit=50)
        search_list = []
        async for search, percent_match in AsyncIter(search_results):
            async for queue_position, title in AsyncIter(search.items()):
                if percent_match > 89:
                    search_list.append((queue_position, title))
        return search_list

    async def _build_queue_search_page(self, ctx: commands.Context, page_num: int, search_list: list[tuple[int, str]]) -> discord.Embed:
        search_num_pages = math.ceil(len(search_list) / 10)
        search_idx_start = (page_num - 1) * 10
        search_idx_end = search_idx_start + 10
        track_match = ""
        async for i, track in AsyncIter(search_list[search_idx_start:search_idx_end]).enumerate(start=search_idx_start):
            track_idx = i + 1
            if isinstance(track, str):
                track_location = LocalPath(track, self.local_folder_current_path).to_string_user()
                track_match += f"`{track_idx}.` **{track_location}**\n"
            else:
                track_match += f"`{track[0]}.` **{track[1]}**\n"
        embed = discord.Embed(colour=await ctx.embed_colour(), title="Matching Tracks:", description=track_match)
        embed.set_footer(
            text=("Page {page_num}/{total_pages} | {num_tracks} tracks").format(
                page_num=humanize_number(page_num),
                total_pages=humanize_number(search_num_pages),
                num_tracks=len(search_list),
            ),
        )
        return embed
