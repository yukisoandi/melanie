from __future__ import annotations

import asyncio
import time
from copy import copy
from typing import Optional

import discord
import regex as re
from discord.utils import sleep_until
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.commands.converter import TimedeltaConverter
from melaniebot.core.config import Config
from regex.regex import Pattern

SLEEP_FLAG: Pattern[str] = re.compile(r"(?:--|—)sleep (\d+)$")


class PhenUtils(commands.Cog):
    """Various developer utilities."""

    __version__ = "1.0.0"

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=623469945234523465, force_registration=True)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def do(self, ctx: commands.Context, times: int, *, command: str) -> None:
        """Repeats a command a specified number of times."""
        return await ctx.invoke(self.bot.get_command("jsk repeat"), times=times, command_string=command)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def execute(self, ctx, sequential: Optional[bool] = False, *, commands) -> None:
        """Execute multiple commands at once.

        Split them using |.

        """
        commands = commands.split("|")
        if sequential:
            for command in commands:
                new_message = copy(ctx.message)
                new_message.content = ctx.prefix + command.strip()
                await self.bot.process_commands(new_message)
        else:
            todo = []
            for command in commands:
                new_message = copy(ctx.message)
                new_message.content = ctx.prefix + command.strip()
                todo.append(self.bot.process_commands(new_message))
            await asyncio.gather(*todo)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def bypass(self, ctx, *, command) -> None:
        """Bypass a command's checks and cooldowns."""
        msg = copy(ctx.message)
        msg.content = ctx.prefix + command

        new_ctx = await self.bot.get_context(msg, cls=type(ctx))
        try:
            await new_ctx.reinvoke()
        except Exception as e:
            await ctx.send(embed=discord.Embed(title="Oops!", description=f"```\n{e}\n```"))

    @commands.is_owner()
    @commands.command(hidden=True)
    async def timing(self, ctx: commands.Context, *, command_string: str):
        """Run a command timing execution and catching exceptions."""
        msg = copy(ctx.message)
        msg.content = ctx.prefix + command_string
        alt_ctx = await self.bot.get_context(msg, cls=type(ctx))

        if alt_ctx.command is None:
            return await ctx.send(f'Command "{alt_ctx.invoked_with}" is not found')

        start = time.perf_counter()

        await alt_ctx.reinvoke()

        # async with ReplResponseReactor(ctx.message):
        #    with self.submit(ctx):

        end = time.perf_counter()
        return await ctx.send(f"Command `{alt_ctx.command.qualified_name}` finished in {end - start:.3f}s.")

    @commands.is_owner()
    @commands.command(aliases=["taskcmd"], hidden=True)
    async def schedulecmd(self, ctx, time: TimedeltaConverter, *, command) -> None:
        """Schedule a command to be done later."""
        end = ctx.message.created_at + time
        new_message = copy(ctx.message)
        new_message.content = ctx.prefix + command.strip()
        await sleep_until(end)
        await self.bot.process_commands(new_message)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def reinvoke(self, ctx: commands.Context, message: discord.Message = None) -> None:
        """Reinvoke a command message.

        You may reply to a message to reinvoke it or pass a message
        ID/link.

        """
        if not message:
            if hasattr(ctx.message, "reference") and (ref := ctx.message.reference):
                message = ref.resolved or await ctx.bot.get_channel(ref.channel_id).fetch_message(ref.message_id)
            else:
                raise commands.BadArgument
        await self.bot.process_commands(message)

    @reinvoke.before_invoke
    async def reinvoke_before_invoke(self, ctx: commands.Context) -> None:
        if not ctx.guild.chunked:
            await ctx.guild.chunk()
