from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

import discord
from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from unidecode import unidecode

from melanie import cancel_tasks, spawn_task
from melanie.timing import capturetime
from nicknamer.nicknamer import GuildSettings

if TYPE_CHECKING:
    from nicknamer.nicknamer import NickNamer


class NickNamerWorker(commands.Cog):
    """Nicknamer's worker."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.nick_cache = {}
        self.config_cache: dict[int, GuildSettings] = {}
        self.frozen_cache = defaultdict(dict)
        self.locks = defaultdict(asyncio.Lock)
        self.active_tasks = []
        spawn_task(self.refresh_config(), self.active_tasks)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def set_frozen_cache(self, guild_id):
        settings = self.config_cache[guild_id]
        self.frozen_cache[int(guild_id)].clear()
        for e in settings.frozen:
            user = e[0]
            self.frozen_cache[int(guild_id)][int(user)] = e[1]
        self.frozen_cache[int(guild_id)][self.bot.user.id] = "melanie"

    async def refresh_config(self, guild=None) -> None:
        await self.bot.waits_uptime_for(12)
        nicknamer: NickNamer = self.bot.get_cog("NickNamer")
        if not nicknamer:
            await self.bot.load_extension("nicknamer")
        if guild:
            self.config_cache[guild.id] = GuildSettings.parse_obj(await nicknamer.config.guild(guild).all())
            await self.set_frozen_cache(guild.id)
        else:
            with capturetime("Global refresh"):
                all_guilds = await nicknamer.config.all_guilds()
                for gid, data in all_guilds.items():
                    self.config_cache[gid] = GuildSettings.parse_obj(data)
                    await self.set_frozen_cache(gid)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.bot.is_ready():
            return
        await self.do_nick_clean_or_lock(after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.bot.is_ready():
            return
        if not message.guild:
            return
        await self.do_nick_clean_or_lock(message.author)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not self.bot.is_ready():
            return
        await self.do_nick_clean_or_lock(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        key = f"{member.id}{member.guild.id}"
        if key in self.nick_cache:
            del self.nick_cache[key]

    async def do_nick_clean_or_lock(self, after: discord.Member):
        if not hasattr(after, "guild"):
            return
        guild: discord.Guild = after.guild
        me: discord.Member = guild.me
        if after.id == me.id and after.nick:
            return await after.edit(nick=None)
        if guild.id not in self.config_cache:
            return
        settings: GuildSettings = self.config_cache[guild.id]
        if guild.owner_id == after.id:
            return
        try:
            async with asyncio.timeout(5):
                if after.id in self.frozen_cache[guild.id]:
                    if after.nick == self.frozen_cache[guild.id][after.id]:
                        return
                    if me.top_role <= after.top_role:
                        return
                    return await after.edit(nick=self.frozen_cache[guild.id][after.id], reason="Nickname frozen.")
                if "afk" in str(after.display_name).lower():
                    return
                if not settings.monitor_nicks:
                    return

                if after.display_name:
                    cleaned = unidecode(after.display_name)
                    if cleaned and not cleaned[0].isalnum():
                        cleaned = None

                    if cleaned != after.display_name:
                        await after.edit(nick=after.name, reason=f"Auto nick cleaning (v2) enabled. Original nick: {after.display_name[:32]}")

        except TimeoutError:
            log.debug("Timeout cleaning the nick {} @ {}", str(after), str(after.guild))
