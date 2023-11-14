from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, DefaultDict, Optional, Union

import discord
import regex as re
import TagScriptEngine as tse
from loguru import logger as log
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from regex.regex import Pattern

from melanie import CurlError, create_task, make_e
from melanie.api_helpers.discord.message import DiscordAPIMessage
from melanie.models.base import BaseModel

from .converters import FuzzyRole

DISBOARD_BOT_ID = 302050872383242240
LOCK_REASON = "DisboardReminder auto-lock"
MENTION_RE: Pattern[str] = re.compile(r"<@!?(\d{15,20})>")
BUMP_RE: Pattern[str] = re.compile(r"!d bump\b")
WARNING_EMBED = make_e("Cleaning up the Disboard message...", status=2, tip="disable this with ;dbump clean")


class GuildSettings(BaseModel):
    channel: int = None
    role: int = None
    message: str = "we haven't bumped in a while. can someone run `/bump`?"
    tyMessage: str = "thanks for bumping!"
    nextBump: float = None


class MemberSettings(BaseModel):
    count: int = 0
    last_bump: float = 0


class DisboardReminder(commands.Cog):
    """Set a reminder to bump on Disboard."""

    __version__ = "1.3.6"
    default_guild_cache = {"channel": None, "tasks": {}}
    default_guild = GuildSettings().dict()

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9765573181940385953309, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.config.register_member(**MemberSettings().dict())

        self.channel_cache = {}
        self.bump_tasks: DefaultDict[int, dict[str, asyncio.Task]] = defaultdict(dict)

        blocks = [tse.LooseVariableGetterBlock(), tse.AssignmentBlock(), tse.IfBlock(), tse.EmbedBlock()]
        self.tagscript_engine = tse.Interpreter(blocks)

        self.bump_loop = create_task(self.bump_check_loop())

    def cog_unload(self) -> None:
        with log.catch(exclude=asyncio.CancelledError):
            self.__unload()

    def __unload(self) -> None:
        if self.bump_loop:
            self.bump_loop.cancel()
        for tasks in self.bump_tasks.values():
            for task in tasks.values():
                task.cancel()

    async def initialize(self) -> None:
        async for guild_id, guild_data in AsyncIter((await self.config.all_guilds()).items(), steps=20):
            if guild_data["channel"]:
                self.channel_cache[guild_id] = guild_data["channel"]

    async def bump_check_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            with log.catch(exclude=asyncio.CancelledError):
                await self.bump_check_guilds()
                await asyncio.sleep(60)

    async def bump_check_guilds(self) -> None:
        async for guild_id, guild_data in AsyncIter((await self.config.all_guilds()).items()):
            if not (guild := self.bot.get_guild(guild_id)):
                continue
            await self.bump_check_guild(guild, guild_data)

    async def bump_check_guild(self, guild: discord.Guild, guild_data: dict) -> None:
        # task logic taken from melaniebot.cogs.mutes
        end_time = guild_data["nextBump"]
        if not end_time:
            return
        now = datetime.utcnow().timestamp()
        remaining = end_time - now
        if remaining > 60:
            return

        # if remaining <= 0:
        #    if task_name in self.bump_tasks[guild.id]:
        task_name = f"bump_timer:{guild.id}-{end_time}"
        if task_name in self.bump_tasks[guild.id]:
            return
        task = create_task(self.bump_timer(guild, end_time), name=task_name)

        self.bump_tasks[guild.id][task_name] = task
        await asyncio.sleep(0.2)

    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.group(aliases=["dbump"])
    async def bumpreminder(self, ctx) -> None:
        """Set a reminder to bump on Disboard.

        This sends a reminder to bump in a specified channel 2 hours
        after someone successfully bumps, thus making it more accurate
        than a repeating schedule.

        """

    @commands.guild_only()
    @bumpreminder.command(aliases=["bprmtop", "bprmlb"])
    async def top(self, ctx: commands.Context, amount: int = 10):
        """View the top Bumpers in the server."""
        if amount < 1:
            raise commands.BadArgument

        members_data = await self.config.all_members(ctx.guild)
        members_list = [(member, data["count"]) for member, data in members_data.items() if ctx.guild.get_member(int(member))]
        ordered_list = sorted(members_list, key=lambda m: m[1], reverse=True)[:(amount)]
        mapped_strings = [f"{index}. <@{member[0]}>: {member[1]}" for index, member in enumerate(ordered_list, start=1)]

        if not mapped_strings:
            return await ctx.send(embed=make_e("No bumps have been tracked yet for this server", 2))
        color = 3092790
        leaderboard_string = "\n".join(mapped_strings)
        if len(leaderboard_string) > 2048:
            embeds = []
            leaderboard_pages = list(pagify(leaderboard_string))
            for index, page in enumerate(leaderboard_pages, start=1):
                embed = discord.Embed(color=color, title="Bump Leaderboard", description=page)
                embed.set_footer(
                    icon_url="https://cdn.discordapp.com/attachments/782123801319440384/839483147740905492/839437202164547654.gif",
                    text=f"{index}/{len(leaderboard_pages)}",
                )
                embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
                embeds.append(embed)
            await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            embed = discord.Embed(color=color, title="Bump Leaderboard", description=leaderboard_string)
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
            await ctx.send(embed=embed)

    @bumpreminder.command(name="channel")
    async def bumpreminder_channel(self, ctx, channel: discord.TextChannel = None) -> None:
        """Set the channel to send bump reminders to.

        This also works as a toggle, so if no channel is provided, it
        will disable reminders for this server.

        """
        if not channel and ctx.guild.id in self.channel_cache:
            del self.channel_cache[ctx.guild.id]
            await self.config.guild(ctx.guild).channel.clear()
            await ctx.send("Disabled bump reminders in this server.")
        elif channel:
            try:
                await channel.send(
                    "Set this channel as the reminder channel for bumps. I will not send my first reminder until a successful bump is registered.",
                )
            except discord.errors.Forbidden:
                await ctx.send("I do not have permission to talk in that channel.")
            else:
                await self.config.guild(ctx.guild).channel.set(channel.id)
                self.channel_cache[ctx.guild.id] = channel.id
                await ctx.tick()
        else:
            raise commands.BadArgument

    @commands.has_permissions(mention_everyone=True)
    @bumpreminder.command(name="pingrole")
    async def bumpreminder_pingrole(self, ctx: commands.Context, role: FuzzyRole = None) -> None:
        """Set a role to ping for bump reminders.

        If no role is provided, it will clear the current role.

        """
        if not role:
            await self.config.guild(ctx.guild).role.clear()
            await ctx.send("Cleared the role for bump reminders.")
        else:
            await self.config.guild(ctx.guild).role.set(role.id)
            await ctx.send(f"Set {role.name} to ping for bump reminders.")

    @bumpreminder.command(name="thankyou", aliases=["ty"])
    async def bumpreminder_thankyou(self, ctx, *, message: str = None) -> None:
        """Change the message used for 'Thank You' messages. Providing no message
        will reset to the default message.

        Variables:
        `{member}` - The user who bumped
        `{server}` - This server

        **Examples:**
        > `;dbump ty Thanks {member} for bumping! You earned 10 brownie points from phen!`

        """
        if message:
            await self.config.guild(ctx.guild).tyMessage.set(message)
            await ctx.tick()
        else:
            await self.config.guild(ctx.guild).tyMessage.clear()
            await ctx.send("Reset this server's Thank You message.")

    @bumpreminder.command(name="message")
    async def bumpreminder_message(self, ctx, *, message: str = None) -> None:
        """Change the message used for reminders.

        Providing no message will reset to the default message.

        """
        if message:
            await self.config.guild(ctx.guild).message.set(message)
            await ctx.tick()
        else:
            await self.config.guild(ctx.guild).message.clear()
            await ctx.send("Reset this server's reminder message.")

    @bumpreminder.command(name="settings")
    async def bumpreminder_settings(self, ctx: commands.Context) -> None:
        """Show your Bump Reminder settings."""
        data = await self.config.guild(ctx.guild).all()
        guild = ctx.guild

        channel = channel.mention if (channel := guild.get_channel(data["channel"])) else "None"
        pingrole = pingrole.mention if (pingrole := guild.get_role(data["role"])) else "None"

        description = [f"**Channel:** {channel}", f"**Ping Role:** {pingrole}"]
        description = "\n".join(description)

        e = discord.Embed(color=await ctx.embed_color(), title="Bump Reminder Settings", description=description)
        e.set_author(name=ctx.guild, icon_url=ctx.guild.icon_url)

        for key, value in data.items():
            if isinstance(value, str):
                value = f"```{discord.utils.escape_markdown(value)}```"
                e.add_field(name=key, value=value, inline=False)
        if data["nextBump"]:
            timestamp = datetime.fromtimestamp(data["nextBump"])
            e.timestamp = timestamp
            e.set_footer(text="Next bump registered for")
        await ctx.send(embed=e)

    async def bump_timer(self, guild: discord.Guild, timestamp: int) -> None:
        d = datetime.fromtimestamp(timestamp)
        await discord.utils.sleep_until(d)
        await self.bump_remind(guild)

    @staticmethod
    async def set_my_permissions(guild: discord.Guild, channel: discord.TextChannel, my_perms: discord.Permissions) -> None:
        if not my_perms.send_messages:
            my_perms.update(send_messages=True)
            await channel.set_permissions(guild.me, overwrite=my_perms, reason=LOCK_REASON)

    async def bump_remind(self, guild: discord.Guild) -> None:
        guild = self.bot.get_guild(guild.id)
        if not guild:
            return
        data = await self.config.guild(guild).all()
        channel = guild.get_channel(data["channel"])

        if not channel:
            return
        my_perms = channel.permissions_for(guild.me)
        if not my_perms.send_messages:
            await self.config.guild(guild).channel.clear()
            return

        message = data["message"]
        allowed_mentions = self.bot.allowed_mentions
        if data["role"] and (role := guild.get_role(data["role"])):
            message = f"{role.mention}: {message}"
            allowed_mentions = discord.AllowedMentions(roles=[role])

        kwargs = self.process_tagscript(message)
        if not kwargs:
            # in case user inputted tagscript returns nothing
            await self.config.guild(guild).message.clear()
            kwargs = self.process_tagscript(self.default_guild["message"])
        kwargs["allowed_mentions"] = allowed_mentions

        try:
            await channel.send(**kwargs)
        except discord.Forbidden:
            await self.config.guild(guild).channel.clear()
        await self.config.guild(guild).nextBump.clear()

    def validate_cache(self, message: discord.Message) -> Optional[discord.TextChannel]:
        guild: discord.Guild = message.guild
        if not guild:
            return
        if message.author.id != DISBOARD_BOT_ID:
            return
        if bump_chan_id := self.channel_cache.get(guild.id):
            return guild.get_channel(bump_chan_id)
        else:
            return

    def validate_success(self, message: discord.Message) -> Union[discord.Embed, discord.Message]:
        if not message.embeds and "Bump done" in message.content:
            return message
        if message.embeds:
            embed = message.embeds[0]
        if ":thumbsup:" in embed.description:
            return embed

    async def respond_to_bump(
        self,
        data: dict,
        bump_channel: discord.TextChannel,
        message: discord.Message,
        embed: Union[discord.Embed, discord.Message],
    ) -> None:
        guild: discord.Guild = message.guild
        my_perms = bump_channel.permissions_for(guild.me)
        next_bump = message.created_at.timestamp() + 7200
        await self.config.guild(guild).nextBump.set(next_bump)
        member_adapter = None
        if isinstance(embed, discord.Message):
            match = MENTION_RE.search(message.content)
        if isinstance(embed, discord.Embed):
            match = MENTION_RE.search(embed.description)

        if match:
            member_id = int(match.group(1))
            user = await self.bot.get_or_fetch_member(guild, member_id)
            member_adapter = tse.MemberAdapter(user)
        elif my_perms.read_message_history:
            async for m in bump_channel.history(before=message, limit=10):
                if m.content and BUMP_RE.match(m.content):
                    member_adapter = tse.MemberAdapter(m.author)
                    break
        if member_adapter is None:
            member_adapter = tse.StringAdapter("Unknown User")

        if my_perms.send_messages:
            guild_adapter = tse.GuildAdapter(guild)
            seed = {"member": member_adapter, "guild": guild_adapter, "server": guild_adapter}
            tymessage = data["tyMessage"]

            kwargs = self.process_tagscript(tymessage, seed_variables=seed)
            if not kwargs:
                # in case user inputted tagscript returns nothing
                await self.config.guild(guild).tyMessage.clear()
                kwargs = self.process_tagscript(self.default_guild["tyMessage"], seed_variables=seed)
            try:
                msg = await DiscordAPIMessage.find(self.bot, message.channel.id, message.id)
                if msg and msg.interaction:
                    async with self.config.member_from_ids(guild.id, int(msg.interaction.user.id)).all() as settings:
                        settings["count"] += 1
                        settings["last_bump"] = time.time()
                        log.success("Tracked bump for {} @ {} ", msg.interaction.user.id, message.guild.id)
            except CurlError as e:
                log.warning("Curl error from API {}", e)
            await message.channel.send(**kwargs)

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message) -> None:
        bump_channel = self.validate_cache(message)
        if not bump_channel:
            return

        guild: discord.Guild = message.guild
        channel: discord.TextChannel = message.channel

        data = await self.config.guild(guild).all()
        if not data["channel"]:
            return
        channel.permissions_for(guild.me)
        if embed_or_msg := self.validate_success(message):
            last_bump = data["nextBump"]
            if last_bump and last_bump - message.created_at.timestamp() > 0:
                return
            await self.respond_to_bump(data, bump_channel, message, embed_or_msg)

    def process_tagscript(self, content: str, *, seed_variables: dict = None) -> dict[str, Any]:
        if seed_variables is None:
            seed_variables = {}
        output = self.tagscript_engine.process(content, seed_variables)
        kwargs = {}
        if output.body:
            kwargs["content"] = output.body[:2000]
        if embed := output.actions.get("embed"):
            kwargs["embed"] = embed
        return kwargs
