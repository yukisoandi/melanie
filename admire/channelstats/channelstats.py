from __future__ import annotations

import asyncio
import random
import time
from contextlib import suppress
from typing import Optional, Union

import discord
from aiomisc.periodic import PeriodicCallback
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import BaseModel, checkpoint, intcomma, log, make_e


class ChannelSettings(BaseModel):
    channel_id: Optional[int] = None
    token_string: Optional[str] = None
    intword: bool = False
    last_updated: Optional[float] = None
    intcomma: bool = False


class ChannelStats(commands.Cog):
    """Channel names with server stats."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=879271957, force_registration=True)
        self.conf.register_channel(**ChannelSettings().dict())
        self.init_cb = PeriodicCallback(self.init)
        self.init_cb.start(30)
        self.channel_callbacks: dict[int, PeriodicCallback] = {}

    def cog_unload(self):
        self.init_cb.stop(True)
        for channel in self.channel_callbacks.values():
            channel.stop(True)

    async def init(self):
        await self.bot.wait_until_ready()
        await self.bot.waits_uptime_for(60)
        all_channels = await self.conf.all_channels()
        for channel_id, data in all_channels.items():
            settings = ChannelSettings.parse_obj(data)
            if not settings.channel_id:
                settings.channel_id = channel_id
            channel: discord.VoiceChannel = self.bot.get_channel(channel_id)
            if settings.token_string and channel and settings.channel_id not in self.channel_callbacks:
                self.channel_callbacks[settings.channel_id] = PeriodicCallback(self.publish_channel_update, channel_id)
                self.channel_callbacks[settings.channel_id].start(600, delay=random.uniform(5, 10))
                log.success("Created periodic callback for channel {}", self.bot.get_channel(settings.channel_id))

    async def publish_channel_update(self, channel: Union[discord.TextChannel, discord.CategoryChannel]):
        if isinstance(channel, int):
            channel = self.bot.get_channel(channel)
            if not channel:
                return
        elif not self.bot.get_channel(channel.id):
            return
        guild: discord.Guild = channel.guild
        if not guild.me.guild_permissions.administrator:
            return log.error("Refusing to publish channel updates for this server. I dont have administrator")
        async with self.conf.channel(channel).all() as _settings:
            settings = ChannelSettings(**_settings)
            if not settings.token_string or "<count>" not in settings.token_string:
                return
            member_count = len([m for m in guild.members if not m.bot])
            member_count = intcomma(member_count)
            new_name = settings.token_string.replace("<count>", member_count)
            with suppress(asyncio.TimeoutError):
                async with asyncio.timeout(5):
                    await channel.edit(name=new_name)
                    settings.last_updated = time.time()
            _settings.update(**settings.dict())

    @commands.command(usage="{CHANNEL_ID} <count> melanie niggas")
    @checks.has_permissions(manage_channels=True)
    async def statschannel(
        self,
        ctx: commands.Context,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel],
        *,
        formatvalue: str,
    ):
        """Configure a voice channel to be updated with server stats at a regular
        interval.

        Channels will update by default approximately every 10 minutes.

        """
        if "<count>" not in formatvalue:
            return await ctx.send(embed=make_e("You need to provide the token `<count>` in your format string so I know what to name the channel to.", 2))

        async with self.conf.channel(channel).all() as _settings:
            settings = ChannelSettings(**_settings)
            settings.token_string = formatvalue
            settings.channel_id = channel.id
            _settings.update(**settings.dict())
        if await self.bot.redis.ratelimited("channel_updates", 2, 500):
            await ctx.send(embed=make_e("Discord only lets me update channels about 2x ever 10 minutes, so this update will be reflected in the next cycle", 2))
        if channel.id in self.channel_callbacks:
            self.channel_callbacks[channel.id].stop(True)
            await checkpoint()
        self.channel_callbacks[channel.id] = PeriodicCallback(self.publish_channel_update, channel.id)
        self.channel_callbacks[channel.id].start(600)
        return await ctx.send(embed=make_e(f"Updated to reflect server stats @ {channel.mention} with token string `{formatvalue}`"))
