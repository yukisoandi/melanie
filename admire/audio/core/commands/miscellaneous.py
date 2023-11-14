from __future__ import annotations

import datetime
import heapq
import math

import discord
import lavalink
from melaniebot.core import commands
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import humanize_number, pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu

from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass


def _(x):
    return x


class MiscellaneousCommands(MixinMeta, metaclass=CompositeMetaClass):
    @commands.command(name="audiostats")
    @commands.guild_only()
    @commands.is_owner()
    async def command_audiostats(self, ctx: commands.Context):
        """Audio stats."""
        server_num = len(lavalink.active_players())
        total_num = len(lavalink.all_connected_players())

        msg = ""
        async for p in AsyncIter(lavalink.all_connected_players()):
            connect_dur = self.get_time_string(int((datetime.datetime.now(datetime.timezone.utc) - p.connected_at).total_seconds())) or "0s"
            try:
                if not p.current:
                    raise AttributeError
                current_title = await self.get_track_description(p.current, self.local_folder_current_path)
                msg += f"{p.guild.name} [`{connect_dur}`]: {current_title}\n"
            except AttributeError:
                msg += f"{p.guild.name} [`{connect_dur}`]: **Nothing playing.**\n"

        if total_num == 0:
            return await self.send_embed_msg(ctx, title="Not connected anywhere.")
        servers_embed = []
        pages = 1
        for page in pagify(msg, delims=["\n"], page_length=1500):
            em = discord.Embed(
                colour=await ctx.embed_colour(),
                title=("Playing in {num}/{total} servers:").format(num=humanize_number(server_num), total=humanize_number(total_num)),
                description=page,
            )
            em.set_footer(text=f"Page {humanize_number(pages)}/{humanize_number(math.ceil(len(msg) / 1500))}")
            pages += 1
            servers_embed.append(em)

        await menu(ctx, servers_embed, DEFAULT_CONTROLS)

    @commands.command(name="percent")
    @commands.guild_only()
    async def command_percent(self, ctx: commands.Context):
        """Queue percentage."""
        if not self._player_check(ctx):
            return await self.send_embed_msg(ctx, title="Nothing playing.")
        player = lavalink.get_player(ctx.guild.id)
        queue_tracks = player.queue
        requesters = {"total": 0, "users": {}}

        async def _usercount(req_username) -> None:
            if req_username in requesters["users"]:
                requesters["users"][req_username]["songcount"] += 1
            else:
                requesters["users"][req_username] = {"songcount": 1}

            requesters["total"] += 1

        async for track in AsyncIter(queue_tracks):
            req_username = f"{track.requester.name}#{track.requester.discriminator}"
            await _usercount(req_username)

        try:
            req_username = f"{player.current.requester.name}#{player.current.requester.discriminator}"
            await _usercount(req_username)
        except AttributeError:
            return await self.send_embed_msg(ctx, title="There's nothing in the queue.")

        async for req_username in AsyncIter(requesters["users"]):
            percentage = float(requesters["users"][req_username]["songcount"]) / float(requesters["total"])
            requesters["users"][req_username]["percent"] = round(percentage * 100, 1)

        top_queue_users = heapq.nlargest(
            20,
            [(x, requesters["users"][x][y]) for x in requesters["users"] for y in requesters["users"][x] if y == "percent"],
            key=lambda x: x[1],
        )
        queue_user = [f"{x[0]}: {x[1]:g}%" for x in top_queue_users]
        queue_user_list = "\n".join(queue_user)
        await self.send_embed_msg(ctx, title="Queued and playing tracks:", description=queue_user_list)
