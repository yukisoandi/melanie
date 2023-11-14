from __future__ import annotations

import asyncio
import time

import discord
from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from xxhash import xxh32_hexdigest

from melanie import BaseModel, capturetime, default_lock_cache, spawn_task
from vanity.vanity import Vanity


class Channel(BaseModel):
    name: str
    id: int


class Guild(BaseModel):
    name: str
    id: int
    num_members: int

    channels: dict[int, Channel]


class TessaWorker(BaseModel):
    name: str
    num_guilds: int


class GuildSettings(BaseModel):
    vanityString: str = ""
    awardedRole: int = None
    notificationChannel: int = None
    enabled: bool = False
    blacklist: list = []
    num_msg_before_award: int = None


class MemberSettings(BaseModel):
    notified: bool = False
    threshold_passed: bool = False


class TemplateParser(BaseModel):
    @classmethod
    def parse_dict(cls, data: dict):
        discord.Embed.from_dict(data)


class VanityWorker(commands.Cog):
    """For level 3 servers, award your users for advertising the vanity in their
    status.

    v1.2

    """

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config_cache: dict[int, GuildSettings] = {}
        self.member_cache: dict[str, MemberSettings] = {}
        self.locks = default_lock_cache()
        self.active_tasks = []
        self.update_cnt = 0

        self.error_cnt = 0

        spawn_task(self.refresh_config(), self.active_tasks)

    def cog_unload(self) -> None:
        for t in self.active_tasks:
            t.cancel()

    @log.catch(reraise=True)
    async def refresh_config(self, guild=None) -> None:
        await self.bot.waits_uptime_for(30)
        with capturetime(f'Refresh configuration {guild or "all"}'):
            vanity: Vanity = self.bot.get_cog("Vanity")
            if guild:
                data = await vanity.config.guild(guild).all()
                self.config_cache[guild.id] = GuildSettings(**data)
                log.warning("Refreshed config for {}", guild)
            else:
                for guild in self.bot.guilds:
                    data = await vanity.config.guild(guild).all()
                    self.config_cache[guild.id] = GuildSettings(**data)

    async def set_member_setting(self, member: discord.Member, settings: MemberSettings) -> None:
        """Update DB and cache with MemberSettings."""
        key = f"{member.guild.id}{member.id}"
        vanity: Vanity = self.bot.get_cog("Vanity")

        async with vanity.config.member(member).all(acquire_lock=False) as member_data:
            member_data.update(**settings.dict())
        self.member_cache[key] = settings

    async def get_member_settings(self, member: discord.Member) -> MemberSettings:
        """Fetch cached member settings."""
        key = f"{member.guild.id}{member.id}"
        if key not in self.member_cache:
            vanity: Vanity = self.bot.get_cog("Vanity")
            data = await vanity.config.member(member).all()
            self.member_cache[key] = MemberSettings(**data)
        return self.member_cache[key]

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:  # sourcery no-metrics
        if not self.bot.is_ready():
            return
        if after.bot:
            return
        guild: discord.Guild = after.guild
        if "VANITY_URL" not in guild.features:
            return
        settings = self.config_cache.get(guild.id)
        if not settings:
            return
        if after.id in settings.blacklist:
            return

        if not settings.enabled or not settings.awardedRole or not settings.vanityString or not settings.notificationChannel:
            return
        vanity_string = str(settings.vanityString)
        role: discord.Role = guild.get_role(settings.awardedRole)
        me: discord.Member = guild.me
        if not role:
            return
        try:
            async with asyncio.timeout(10):
                prekey = f"{after.id}{guild.id}{role.id}"
                key = f"vanity_roleadd:{xxh32_hexdigest(prekey)}"
                if role in after.roles and vanity_string not in str(after.activity):
                    await self.bot.redis.set(key, int(time.time()), ex=300)
                    return await after.remove_roles(role)
                if role not in after.roles and vanity_string in str(after.activity):
                    if role >= me.top_role:
                        return

                    await self.bot.redis.set(key, int(time.time()), ex=300)
                    await after.add_roles(role)
                    award_channel = guild.get_channel(settings.notificationChannel)
                    if not award_channel:
                        return
                    notify_key = f"vanityalert2:{guild.id}{after.id}"
                    if await self.bot.redis.get(notify_key):
                        return
                    awardEmbed = discord.Embed(title="vanity set", description=f"thank you {after.mention}", color=3092790)
                    awardEmbed.set_footer(text=f"put {vanity_string} in your status for the {role.name} role.")
                    await award_channel.send(embed=awardEmbed)
                    await self.bot.redis.set(notify_key, 1, ex=14400)
        except TimeoutError:
            return log.warning("Timedout running update task @ {} / {}", after, after.guild)
