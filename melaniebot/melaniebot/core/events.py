from __future__ import annotations

import contextlib
import os
import traceback
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from aiomisc.backoff import asyncretry
from loguru import logger as log
from melanie import normalize_smartquotes
from melanie.helpers import make_e

from melaniebot.core import commands

from .config import get_latest_confs
from .utils import AsyncIter
from .utils.chat_formatting import format_perms_list, humanize_timedelta

if TYPE_CHECKING:
    from melaniebot.core.bot import Melanie
INTRO = """
            Bot is chunked and ready.

                       888                   d8b
                       888                   Y8P
                       888
88888b.d88b.   .d88b.  888  8888b.  88888b.  888  .d88b.
888 "888 "88b d8P  Y8b 888     "88b 888 "88b 888 d8P  Y8b
888  888  888 88888888 888 .d888888 888  888 888 88888888
888  888  888 Y8b.     888 888  888 888  888 888 Y8b.
888  888  888  "Y8888  888 "Y888888 888  888 888  "Y8888


"""


def init_events(bot: Melanie, cli_flags):
    @bot.event
    async def on_connect():
        if bot._uptime is not None:
            return
        os.environ.update(BOT_NAME=str(bot.user))
        os.environ.update(BOT_USER=str(bot.user))
        log.info("Connected to Discord. Getting ready...")
        bot._red_ready.set()
        bot._ready.set()

    @bot.event
    async def on_ready():
        if bot._uptime is not None:
            return
        bot._uptime = datetime.utcnow()
        app_info = await bot.application_info()
        bot.owner_ids.update(m.id for m in app_info.team.members)
        bot._app_owners_fetched = True
        try:
            invite_url = discord.utils.oauth_url(app_info.id)
        except Exception:
            invite_url = "Could not fetch invite url"
        prefixes = cli_flags.prefix or (await bot._config.prefix())
        log.info(INTRO)
        log.info(f"Prefixes {prefixes}")
        log.info(f"Loaded {len(bot.cogs)} cogs with {len(bot.commands)} commands")
        if invite_url:
            log.info(f"Invite URL {invite_url}")
        if not bot.owner_ids:
            log.warning("Bot doesn't have any owner set!")
        bot._color = discord.Colour(await bot._config.color())

    @bot.event
    async def on_command_error(ctx: commands.Context, error, unhandled_by_cog=False):
        if hasattr(ctx, "via_event"):
            raise error

        if not unhandled_by_cog:
            if hasattr(ctx.command, "on_error"):
                return
            if ctx.cog and ctx.cog.has_error_handler():
                return
        # if not isinstance(error, commands.CommandNotFound):

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send_help()
        elif isinstance(error, commands.ArgParserFailure):
            msg = f"`{error.user_input}` is not a valid value for `{error.cmd}`"
            if error.custom_help_msg:
                msg += f"\n{error.custom_help_msg}"
                await ctx.send(embed=make_e(msg, 2))
            if error.send_cmd_help:
                await ctx.send_help()
        elif isinstance(error, (commands.ConversionFailure, commands.BadArgument)):
            if error.args:
                await ctx.send(embed=make_e(str(error), 3))
            else:
                await ctx.send_help()
        elif isinstance(error, commands.UserInputError):
            await ctx.send_help()
        elif isinstance(error, commands.DisabledCommand):
            disabled_message = await bot._config.disabled_command_msg()
            if disabled_message:
                await ctx.send(embed=make_e(disabled_message, 2))
        elif isinstance(error, commands.CommandInvokeError):
            exception_log = "Exception in cmd \n" + "".join(traceback.format_exception(type(error), error, error.__traceback__))
            bot._last_exception = exception_log
            log.opt(exception=error).error("Exception in command '{}'", str(ctx.command.qualified_name))

            @asyncretry(max_tries=3, pause=1)
            async def error_reaction():
                with contextlib.suppress(discord.NotFound):
                    await ctx.message.add_reaction("❌")

            await error_reaction()

        elif isinstance(error, commands.BotMissingPermissions):
            if bin(error.missing.value).count("1") == 1:  # Only one perm missing
                msg = f"I require the {format_perms_list(error.missing)} permission to execute that command."
            else:
                msg = f"I require {format_perms_list(error.missing)} permissions to execute that command."
            await ctx.send(embed=make_e(msg, 2))
        elif isinstance(error, commands.UserFeedbackCheckFailure):
            if error.message:
                await ctx.send(embed=make_e(error.message, 2))
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=make_e("That command is not available in DMs.", 2))
        elif isinstance(error, commands.PrivateMessageOnly):
            await ctx.send(embed=make_e("That command is only available in DMs.", 2))
        elif isinstance(error, commands.NSFWChannelRequired):
            await ctx.send(embed=make_e("That command is only available in NSFW channels.", 2))
        elif isinstance(error, commands.CheckFailure):
            pass
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.CommandOnCooldown):
            if bot._bypass_cooldowns and ctx.author.id in bot.owner_ids:
                ctx.command.reset_cooldown(ctx)
                new_ctx = await bot.get_context(ctx.message)
                await bot.invoke(new_ctx)
                return
            if delay := humanize_timedelta(seconds=error.retry_after):
                msg = f"This command is on cooldown. Try again in {delay}."
            else:
                msg = "This command is on cooldown. Try again in 1 second."
            await ctx.send(embed=make_e(msg, 3), delete_after=error.retry_after)
        elif isinstance(error, commands.MaxConcurrencyReached):
            if error.per is commands.BucketType.default:
                if error.number > 1:
                    msg = f"Too many people using this command. It can only be used {error.number} times concurrently."
                else:
                    msg = "Too many people using this command. It can only be used once concurrently."
            elif error.per in (commands.BucketType.user, commands.BucketType.member):
                if error.number > 1:
                    msg = f"That command is still completing, it can only be used {error.number} times per {error.per.name} concurrently."
                else:
                    msg = f"That command is still completing, it can only be used once per {error.per.name} concurrently."
            elif error.number > 1:
                msg = f"Too many people using this command. It can only be used {error.number} times per {error.per.name} concurrently."
            else:
                msg = f"Too many people using this command. It can only be used once per {error.per.name} concurrently."
            await ctx.send(embed=make_e(msg, 3))
        else:
            raise error

    @bot.event
    async def on_message(message: discord.Message):
        message.content: str = normalize_smartquotes(message.content)
        if message.guild and (message.guild.id, message.author.id) in bot._shutup_group:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
            return await bot.redis.set(f"shutup_lock:{message.channel.id}", 1, ex=40)
        await bot.process_commands(message)

    @bot.event
    async def on_command_add(command: commands.Command):
        disabled_commands = await bot._config.disabled_commands()
        if command.qualified_name in disabled_commands:
            command.enabled = False
        guild_data = await bot._config.all_guilds()
        async for guild_id, data in AsyncIter(guild_data.items(), steps=20):
            disabled_commands = data.get("disabled_commands", [])
            if command.qualified_name in disabled_commands:
                command.disable_in(discord.Object(id=guild_id))

    async def _guild_added(guild: discord.Guild):
        disabled_commands = await bot._config.guild(guild).disabled_commands()
        for command_name in disabled_commands:
            command_obj = bot.get_command(command_name)
            if command_obj is not None:
                command_obj.disable_in(guild)

    @bot.event
    async def on_guild_join(guild: discord.Guild):
        await _guild_added(guild)

    @bot.event
    async def on_guild_available(guild: discord.Guild):
        # We need to check guild-disabled commands here since some cogs
        # are loaded prior to `on_ready`.
        await _guild_added(guild)

    @bot.event
    async def on_guild_leave(guild: discord.Guild):
        # Clean up any unneeded checks
        disabled_commands = await bot._config.guild(guild).disabled_commands()
        for command_name in disabled_commands:
            command_obj = bot.get_command(command_name)
            if command_obj is not None:
                command_obj.enable_in(guild)

    @bot.event
    async def on_cog_add(cog: commands.Cog):
        confs = get_latest_confs()
        for c in confs:
            uuid = c.unique_identifier
            group_data = c.custom_groups
            await bot._config.custom("CUSTOM_GROUPS", c.cog_name, uuid).set(group_data)
