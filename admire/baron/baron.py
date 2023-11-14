from __future__ import annotations

import asyncio
import random
from typing import Optional

import discord
from aiomisc import cancel_tasks
from aiomisc.periodic import PeriodicCallback
from boltons.iterutils import unique
from discord import AsyncWebhookAdapter, Webhook
from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import humanize_list, humanize_number, pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu

from melanie.redis import get_redis


def comstats_cog(ctx: commands.Context) -> bool:
    return ctx.bot.get_cog("CommandStats") is not None


def disabled_or_data(data):
    return data or "Disabled"


default_global = {"limit": 0, "log_channel": None, "log_guild": None, "min_members": 0, "bot_ratio": 0, "whitelist": [], "blacklist": []}


class Baron(commands.Cog):
    """Tools for managing guild joins and leaves."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=325236743863625234572, force_registration=True)
        self.config.register_global(**default_global)
        self.active_tasks = []
        self.check_cb = PeriodicCallback(self.check_unwhitelisted)

        self.sync_lock = asyncio.Lock()
        self.webhook = Webhook.from_url(
            "https://discord.com/api/webhooks/1125547123199451177/wB7O0x8E2AFZYM6pFsnF5uzYoWV-m8RKOaOikgT8UXqU0351OXDofAsstjfa7DORZTmH",
            adapter=AsyncWebhookAdapter(self.bot.aio),
        )
        self.blackist_cb = PeriodicCallback(self.set_redis_data)
        self.blackist_cb.start(30)
        self.check_cb.start(34, delay=2)

    def cog_unload(self) -> None:
        self.blackist_cb.stop(True)
        self.check_cb.stop(True)
        cancel_tasks(self.active_tasks)

    async def check_unwhitelisted(self) -> None:
        await self.bot.waits_uptime_for(40)
        redis = get_redis()
        total_whitelist = await redis.scard("guild_whitelist")
        if total_whitelist < 90:
            return log.warning("Whitelist only has {}", total_whitelist)
        webhook = self.webhook
        for g in list(self.bot.guilds):
            await asyncio.sleep(random.uniform(0.5, 1.0))
            g: discord.Guild
            reason = None
            if not await redis.sismember("guild_whitelist", g.id):
                reason = "whitelist"
            elif await redis.sismember("guild_blacklist", g.id):
                reason = "blacklist"
            elif not any([g.get_member(928394879200034856), g.get_member(1093001106809950228)]):
                reason = "missing_main_bot"
            elif not g.me.guild_permissions.administrator:
                reason = "invalid_permissions"
            if reason:
                if not self.bot.get_guild(g.id):
                    continue
                e = discord.Embed()
                if reason == "blacklist":
                    e.title = f"{reason.capitalize()} Guild Leave"
                else:
                    e.title = "Guild Leave"
                e.description = f"Guild: {g} / {g.id}"
                e.set_author(name=str(self.bot.user), icon_url=str(self.bot.user.avatar_url))
                e.add_field(name="reason", value=reason)
                e.add_field(name="owner", value=f"{g.owner} / {g.owner_id}")
                e.add_field(name="bot user", value=str(self.bot.user))
                e.add_field(name="member count", value=g.member_count)
                e.add_field(name="whitelisted", value=await redis.sismember("guild_whitelist", g.id))
                e.color = 0
                log.warning("Leaving {}. Reason: {} Owner: {} ", g, reason, g.owner)
                await webhook.send(embed=e, avatar_url="https://hurt.af/gif/POUT.png", username=str(self.bot.user.name))
                await g.leave()

    @commands.is_owner()
    @commands.group(hidden=True)
    async def baron(self, ctx: commands.Context) -> None:
        """Baron's watchtower."""

    @baron.command()
    async def settings(self, ctx: commands.Context) -> None:
        """View Baron settings."""
        data = await self.config.all()
        log_chan = data["log_channel"]
        if log_chan := self.bot.get_channel(log_chan):
            log_chan = log_chan.mention
        description = [
            f"Log Channel: {log_chan}",
            f"Server Limit: {disabled_or_data(data['limit'])}",
            f"Minimum Members: {disabled_or_data(data['min_members'])}",
            f"Bot Farm: {disabled_or_data(data['bot_ratio'])}",
        ]
        e = discord.Embed(color=await ctx.embed_color(), title="Baron Settings", description="\n".join(description))
        await ctx.send(embed=e)

    @baron.command()
    async def limit(self, ctx: commands.Context, limit: int = 0) -> None:
        """Set the maximum amount of servers the bot can be in.

        Pass 0 to disable.

        """
        await self.config.limit.set(limit)
        await ctx.send(f"The server limit has been set to {limit}." if limit else ("The server limit has been disabled."))

    @baron.command()
    async def channel(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set a log channel for Baron alerts."""
        if channel:
            await self.config.log_channel.set(channel.id)
            await self.config.log_guild.set(channel.guild.id)
            await ctx.send(f"Baron's log channel has been set to {channel.mention}.")
        else:
            await self.config.log_channel.clear()
            await self.config.log_guild.clear()
            await ctx.send("Baron's log channel has been removed.")

    @baron.command(aliases=["wl"])
    async def whitelist(self, ctx: commands.Context, guild_id: int) -> None:
        """Whitelist a server from Baron actions."""
        wl = await self.config.whitelist()
        if guild_id in wl:
            return await ctx.send("This server is already whitelisted.")

        wl.append(guild_id)
        await self.config.whitelist.set(unique(wl))
        await self.set_redis_data()

        await ctx.tick()

    @baron.command(aliases=["unwl"])
    async def unwhitelist(self, ctx: commands.Context, guild_id: int) -> None:
        """Remove a server from the whitelist."""
        whitelist: list = await self.config.whitelist()

        if guild_id not in whitelist:
            return await ctx.send("This server is not in the whitelist.")

        whitelist.remove(guild_id)
        await self.config.whitelist.set(unique(whitelist))

        await self.set_redis_data()

        await ctx.tick()

    @baron.command(aliases=["bl"])
    async def blacklist(self, ctx: commands.Context, guild_id: int = None) -> None:
        """Blacklist the bot from joining a server."""
        if not guild_id:
            e = discord.Embed(color=await ctx.embed_color(), title="Baron Blacklist", description=humanize_list(await self.config.blacklist()))
            await ctx.send(embed=e)
        else:
            if guild_id in await self.config.blacklist():
                await ctx.send("This server is already blacklisted.")
                return
            async with self.config.blacklist() as b:
                b.append(guild_id)
            await ctx.tick()

    @baron.command(aliases=["unbl"])
    async def unblacklist(self, ctx: commands.Context, guild_id: int) -> None:
        """Remove a server from the blacklist."""
        if guild_id not in await self.config.blacklist():
            await ctx.send("This server is not in the blacklist.")
            return
        async with self.config.blacklist() as b:
            index = b.index(guild_id)
            b.pop(index)
        await ctx.tick()

    @baron.command()
    async def minmembers(self, ctx: commands.Context, limit: Optional[int] = 0) -> None:
        """Set the minimum number of members a server should have for the bot to
        stay in it.

        Pass 0 to disable.

        """
        await self.config.min_members.set(limit)
        await ctx.send(f"The minimum member limit has been set to {limit}." if limit else ("The minimum member limit has been disabled."))

    @baron.command()
    async def botratio(self, ctx: commands.Context, ratio: Optional[int] = 0):
        """Set the bot ratio for servers for the bot to leave.

        Pass 0 to disable.

        """
        if ratio not in range(100):
            raise commands.BadArgument
        await self.config.bot_ratio.set(ratio)
        await ctx.send(f"The bot ratio has been set to {ratio}." if ratio else ("The bot ratio has been removed."))

    async def view_guilds(
        self,
        ctx: commands.Context,
        guilds: list[discord.Guild],
        title: str,
        page_length: int = 500,
        *,
        color: discord.Color = discord.Color.blurple(),
        footer: str = None,
        insert_function=None,
    ) -> None:
        page_length = max(100, min(2000, page_length))
        data = await self.config.all()
        whitelist = data["whitelist"]

        desc = []
        async for guild in AsyncIter(guilds, steps=20):
            bots = len([x async for x in AsyncIter(guild.members, steps=20) if x.bot])
            percent = bots / guild.member_count
            guild_desc = [f"{guild.name} - ({guild.id})", f"Members: **{humanize_number(guild.member_count)}**", f"Bots: **{round(percent * 100, 2)}%**"]
            if insert_function:
                guild_desc.append(str(insert_function(guild)))
            if guild.id in whitelist:
                guild_desc.append("[Whitelisted](https://www.youtube.com/watch?v=oHg5SJYRHA0)")
            desc.append("\n".join(guild_desc))

        pages = list(pagify("\n\n".join(desc), ["\n\n"], page_length=page_length))
        embeds = []
        base_embed = discord.Embed(color=color, title=title)
        bot_guilds = self.bot.guilds
        for index, page in enumerate(pages, 1):
            e = base_embed.copy()
            e.description = page
            footer_text = f"{index}/{len(pages)} | {len(guilds)}/{len(bot_guilds)} servers"
            if footer:
                footer_text += f" | {footer}"
            e.set_footer(text=footer_text)
            embeds.append(e)
        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @baron.group(name="view")
    async def baron_view(self, ctx: commands.Context) -> None:
        """View servers with specific details."""

    @baron_view.command(name="botfarms")
    async def baron_view_botfarms(self, ctx: commands.Context, rate: Optional[int] = 75, page_length: Optional[int] = 500):
        """View servers that have a bot to member ratio with the given rate."""
        (bot_farms, ok_guilds) = await self.get_bot_farms(rate / 100)
        if not bot_farms:
            return await ctx.send(f"There are no servers with a bot ratio higher or equal than {rate}%.")
        await self.view_guilds(ctx, bot_farms, f"Bot Farms ({rate}%)", page_length, footer=f"OK guilds: {ok_guilds}")

    @baron_view.command(name="members")
    async def baron_view_members(self, ctx: commands.Context, members: int, less_than: Optional[bool] = True, page_length: Optional[int] = 500):
        """View servers that have a member count less than the specified number.

        Pass `False` at the end if you would like to view servers that
        are greater than the specified number.

        """
        if less_than:
            guilds = [guild async for guild in AsyncIter(self.bot.guilds, steps=20) if guild.member_count < members]
        else:
            guilds = [guild async for guild in AsyncIter(self.bot.guilds, steps=20) if guild.member_count > members]
        if not guilds:
            return await ctx.send(f"There are no servers with a member count {'less' if less_than else 'greater'} than {members}.")
        await self.view_guilds(ctx, guilds, f"Server Members ({members})", page_length)

    @commands.check(comstats_cog)
    @baron_view.command(name="commands")
    async def baron_view_commands(self, ctx: commands.Context, commands: int, highest_first: Optional[bool] = False, page_length: Optional[int] = 500):
        """View servers that have command usage less than the specified number.

        Pass `True` at the end if you would like to view servers in
        order of most commands used.

        """
        cog = self.bot.get_cog("CommandStats")
        data = await cog.config.guilddata()
        guilds = []
        guild_command_usage = {}

        async for guild in AsyncIter(self.bot.guilds, steps=20):
            guild_data = data.get(str(guild.id), {})
            total_commands = sum(guild_data.values())
            if total_commands < commands:
                guilds.append((guild, total_commands))
                guild_command_usage[guild.id] = total_commands
        guilds.sort(key=lambda x: x[1], reverse=highest_first)
        if not guilds:
            return await ctx.send(f"There are no servers that have used less than {commands} commands.")

        def insert_function(guild: discord.Guild) -> str:
            return f"Commands Used: **{guild_command_usage.get(guild.id, 0)}**"

        await self.view_guilds(
            ctx,
            [g async for g, c in AsyncIter(guilds, steps=20)],
            f"Command Usage ({commands})",
            page_length,
            insert_function=insert_function,
        )

    @baron_view.command(name="unchunked")
    async def baron_view_unchunked(self, ctx: commands.Context, page_length: Optional[int] = 500):
        """View unchunked servers."""
        guilds = [g for g in AsyncIter(self.bot.guilds, steps=20) if not g.chunked]
        if not guilds:
            return await ctx.send("There are no unchunked servers.")

        def insert_function(guild: discord.Guild) -> str:
            members = len(guild.members)
            percent = members / guild.member_count
            return f"Members Cached: **{humanize_number(members)} ({round(percent * 100, 2)})%**"

        await self.view_guilds(ctx, guilds, "Unchunked Servers", page_length, insert_function=insert_function)

    @baron_view.command(name="ownedby")
    async def baron_view_ownedby(self, ctx: commands.Context, user: discord.User, page_length: Optional[int] = 500):
        """View servers owned by a user."""
        bot_guilds = self.bot.guilds
        guilds = [g async for g in AsyncIter(bot_guilds, steps=20) if g.owner_id == user.id]
        if not guilds:
            return await ctx.send(f"**{user}** does not own any servers I am in.")

        owned_ratio = len(guilds) / len(bot_guilds)
        await self.view_guilds(ctx, guilds, f"Servers owned by {user}", footer=f"{user} owns {round(owned_ratio * 100, 8)}% of the bot's servers")

    async def set_redis_data(self):
        await self.bot.wait_until_ready()
        if self.bot.user.id == 928394879200034856:
            redis = get_redis()
            whitelist = await self.config.whitelist()
            blacklist = await self.config.blacklist()
            global_blacklist = await self.bot.get_blacklist()
            async with redis.pipeline() as pipe:
                pipe.delete("guild_whitelist")
                pipe.sadd("guild_whitelist", *whitelist)
                pipe.delete("guild_blacklist")
                pipe.sadd("guild_blacklist", *blacklist)
                pipe.delete("global_blacklist")
                pipe.sadd("global_blacklist", *global_blacklist)
                await pipe.execute()

    async def get_bot_farms(self, rate: float) -> tuple[list[discord.Guild], int]:
        bot_farms = []
        ok_guilds = 0
        async for guild in AsyncIter(self.bot.guilds, steps=20):
            bots = len([x async for x in AsyncIter(guild.members, steps=20) if x.bot])
            percent = bots / guild.member_count
            if percent >= rate:
                bot_farms.append(guild)
            else:
                ok_guilds += 1
        return (bot_farms, ok_guilds)

    async def notify_guild(self, guild: discord.Guild, message: str) -> None:
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            await guild.system_channel.send(message)
        else:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    await channel.send(message)
                    break

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if not await self.bot.redis.sismember("guild_whitelist", guild.id):
            g = guild
            e = discord.Embed()
            e.title = "Guild Leave"
            e.description = f"Guild: {g} / {g.id}"
            e.set_author(name=str(self.bot.user), icon_url=str(self.bot.user.avatar_url))
            e.add_field(name="reason", value="whitelist")
            e.add_field(name="owner", value=f"{str(g.owner).replace('#0', '')} / {g.owner_id}")
            e.add_field(name="bot user", value=str(self.bot.user))
            e.add_field(name="member count", value=g.member_count)
            e.add_field(name="whitelisted", value=False)
            e.color = 0
            await self.notify_guild(
                guild,
                "Melanie is a premium bot and cannot be added to servers for free. This server is not whitelisted to join. Join https://discord.gg/melaniebot and purchase a server activation.\nI'll automatically leave this server shortly",
            )
            await self.webhook.send(embed=e, avatar_url="https://hurt.af/gif/POUT.png", username=str(self.bot.user.name))
            await guild.leave()
