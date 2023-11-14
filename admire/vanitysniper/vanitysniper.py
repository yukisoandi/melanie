from __future__ import annotations

import asyncio
import itertools
import random
import socket
import time
from collections import defaultdict

import aiohttp
import discord
import orjson
import tuuid
import yarl
from aiomisc.periodic import PeriodicCallback
from aiomisc.utils import cancel_tasks
from anyio import Path
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from antinuke.antinuke import VanityAntinukeProtectionEvent
from melanie import (
    BaseModel,
    alru_cache,
    capturetime,
    checkpoint,
    create_task,
    default_lock_cache,
    log,
    make_e,
)
from melanie.core import spawn_task


class GuildSettings(BaseModel):
    checking_vanities: list[str] = []
    alert_channel: int = None
    enabled: bool = False


HEADERS = {"Content-Type": "application/json"}


class VanitySniper(commands.Cog):
    """Private vanity sniping commands."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.hidden = True
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.active_tasks: list[asyncio.Task] = []
        self.proxy_cycle = itertools.cycle([True, False])
        self.allowed_guilds = [899833727490867272, 1082796333057970256]
        self.check_tasks: dict[int, list[PeriodicCallback]] = defaultdict(list)
        self.locks = default_lock_cache()
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(10),
            connector=aiohttp.TCPConnector(family=socket.AF_INET, resolver=aiohttp.AsyncResolver(), ttl_dns_cache=300),
        )
        spawn_task(self.init(), self.active_tasks)

    def cog_unload(self):
        create_task(self.cancel_tasks())
        cancel_tasks(self.active_tasks)
        create_task(self.session.close())

    async def init(self):
        with capturetime("loads"):
            f = Path(__file__).with_name("urls.json")
            urls: list = []

            async def check_url(url):
                _url = yarl.URL(url)
                if _url.host and _url.scheme:
                    try:
                        async with asyncio.timeout(10), self.session.post(url, json={"code": "melaniebot"}, headers=HEADERS) as r:
                            if r.status == 404:
                                log.warning("Removing URL {} {}", url, r.status)
                            else:
                                urls.append(str(_url))
                                log.success("URL {} OK", url)
                    except TimeoutError:
                        return log.warning("Timeout @ {}", url)

            async with asyncio.TaskGroup() as tg:
                for url in orjson.loads(await f.read_bytes()):
                    tg.create_task(check_url(url))

            self.url_cycle = itertools.cycle(urls)
            log.info(urls)

            for g in self.bot.guilds:
                await self.start_vanity_checks(g)

    async def cancel_tasks(self, guild=None):
        if not guild:
            for task_list in self.check_tasks.values():
                for cb in task_list:
                    cb.stop(True)
        else:
            for cb in self.check_tasks[guild.id]:
                cb.stop(True)

    @commands.guild_only()
    @commands.group(name="sniper", hidden=True)
    @checks.has_permissions(administrator=True)
    async def sniper(self, ctx: commands.Context) -> None:
        """Sniper."""

    def is_allowed(self, ctx):
        return ctx.guild.id in self.allowed_guilds

    @sniper.command(name="enable")
    async def enable_sniper(self, ctx: commands.Context):
        if not self.is_allowed(ctx):
            return
        state = await self.config.guild(ctx.guild).enabled()
        if state:
            await self.config.guild(ctx.guild).enabled.set(False)
            await self.cancel_tasks(ctx.guild)
            return await ctx.send(embed=make_e("Sniper disabled"))

        await self.config.guild(ctx.guild).enabled.set(True)
        await self.reset_settings(ctx.guild.id)
        await self.start_vanity_checks(ctx.guild)
        return await ctx.send(embed=make_e("Sniper enabled"))

    @sniper.command(name="channel")
    async def alert_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        if not self.is_allowed(ctx):
            return
        await self.config.guild(ctx.guild).alert_channel.set(channel.id)
        await self.reset_settings(ctx.guild.id)
        await self.start_vanity_checks(ctx.guild)
        return await ctx.send(embed=make_e(f"Set the snipe alert channel to {channel.mention}"))

    @sniper.command(name="add")
    async def add_vanity(self, ctx: commands.Context, vanity: str):
        if not self.is_allowed(ctx):
            return
        async with self.config.guild(ctx.guild).all() as _settings:
            settings = GuildSettings(**_settings)
            if len(settings.checking_vanities) > 6:
                return await ctx.send("Max number of vanities configured")

            if vanity in settings.checking_vanities:
                return await ctx.send("That vanity is already being sniped!")
            settings.checking_vanities.append(vanity)
            _settings.update(settings.dict())

        await self.reset_settings(ctx.guild.id)
        await self.start_vanity_checks(ctx.guild)
        await asyncio.sleep(0.1)
        return await ctx.send(f"Added vanity {vanity} to snipe list")

    @sniper.command(name="remove")
    async def remove_vanity(self, ctx: commands.Context, vanity: str):
        if not self.is_allowed(ctx):
            return
        async with self.config.guild(ctx.guild).all() as _settings:
            settings = GuildSettings(**_settings)
            if vanity not in settings.checking_vanities:
                return await ctx.send("That vanity not being sniped.")
            settings.checking_vanities.remove(vanity)
            _settings.update(settings.dict())
        await asyncio.sleep(0.1)
        await self.reset_settings(ctx.guild.id)
        await self.start_vanity_checks(ctx.guild)
        return await ctx.send(f"Removed vanity {vanity} from snipe list")

    @sniper.command(name="settings")
    async def vanity_settings(self, ctx: commands.Context):
        await self.reset_settings(ctx.guild.id)
        if not self.is_allowed(ctx):
            return
        settings = await self.get_settings(ctx.guild.id)
        embed = discord.Embed()
        embed.title = "Configured vanity snipe settings"
        embed.add_field(name="vanities", value="\n".join(settings.checking_vanities))
        embed.add_field(name="alert channel", value=settings.alert_channel)
        embed.add_field(name="enabled", value=settings.enabled)

        return await ctx.send(embed=embed)

    async def reset_settings(self, guild_id):
        self.get_settings.cache_clear()
        await checkpoint()
        keys = await self.bot.redis.keys("vanity_alert*")
        if keys:
            await self.bot.redis.delete(*keys)

    @alru_cache(maxsize=None)
    async def get_settings(self, guild_id):
        return GuildSettings(**await self.config.guild_from_id(guild_id).all())

    async def run_check(self, code, guild_id):
        settings = await self.get_settings(guild_id)
        channel = self.bot.get_channel(settings.alert_channel)
        guild: discord.Guild = self.bot.get_guild(guild_id)
        if not channel:
            return

        if guild.premium_tier != 3:
            return
        if not await self.config.guild(guild).enabled():
            return
        try:
            url = next(self.url_cycle)
            async with self.session.post(url, json={"code": code}, headers=HEADERS, timeout=10) as r:
                if r.status == 404:
                    log.warning("Endpoint {} returns {}", url, r.status)
                    vanity_event = VanityAntinukeProtectionEvent(
                        guild_id=guild_id,
                        target_vanity=code,
                        bad_vanity="None",
                        created_at=time.time(),
                        confirm_key=tuuid.tuuid(),
                        lock=tuuid.tuuid(),
                    )
                    await vanity_event.publish()
                    ack = await vanity_event.wait_for_ack()
                    if ack:
                        await channel.send(
                            f"{guild.default_role} Vanity ack retrived! - Event submitted {vanity_event}. Shutting down sniper!",
                            allowed_mentions=discord.AllowedMentions.all(),
                        )
                        async with self.config.guild(guild).all() as _conf:
                            _conf["checking_vanities"].remove(code)
                            _conf["enabled"] = False

                    else:
                        await channel.send("Confirmation from the worker was never retrived. Please report this")
                        return await self.config.guild(guild).enabled.set(False)

            _key = f"vanity_alert{guild.id}{code}"
            sent = await self.bot.redis.get(_key)
            if not sent:
                await channel.send(f"Checking: {code} Status: {r.status}")
                await self.bot.redis.set(_key, time.time(), ex=random.randint(4, 9))
        except TimeoutError:
            log.warning("Timed out for {}", code)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Unhanled Error {}", type(e))

    async def start_vanity_checks(self, guild: discord.Guild):
        settings = await self.get_settings(guild.id)
        if not settings.enabled:
            return
        await self.cancel_tasks(guild)
        for code in settings.checking_vanities:
            cb = PeriodicCallback(self.run_check, code, guild.id)
            cb.start(3)
            self.check_tasks[guild.id].append(cb)
