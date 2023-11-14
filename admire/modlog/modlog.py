from __future__ import annotations

from typing import Union

import discord
from melaniebot.core import checks, commands, modlog
from melaniebot.core.bot import Melanie
from melaniebot.core.i18n import cog_i18n
from melaniebot.core.utils.chat_formatting import box, pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from melaniebot.core.utils.predicates import MessagePredicate


def _(x):
    return x


@cog_i18n(_)
class ModLog(commands.Cog):
    """Manage log channels for moderation actions."""

    def __init__(self, bot: Melanie) -> None:
        super().__init__()
        self.bot = bot

    @commands.group(hidden=True)
    @checks.guildowner_or_permissions(administrator=True)
    async def modlogset(self, ctx: commands.Context) -> None:
        """Manage modlog settings."""

    @checks.is_owner()
    @modlogset.command(hidden=True, name="fixcasetypes")
    async def reapply_audittype_migration(self, ctx: commands.Context) -> None:
        """Command to fix misbehaving casetypes."""
        await modlog.handle_auditype_key()
        await ctx.tick()

    @modlogset.command(aliases=["channel"])
    @commands.guild_only()
    async def modlog(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set a channel as the modlog.

        Omit `[channel]` to disable the modlog.

        """
        guild = ctx.guild
        if channel:
            if channel.permissions_for(guild.me).send_messages:
                await modlog.set_modlog_channel(guild, channel)
                await ctx.send(f"Mod events will be sent to {channel.mention}.")
            else:
                await ctx.send(f"I do not have permissions to send messages in {channel.mention}!")
        else:
            try:
                await modlog.get_modlog_channel(guild)
            except RuntimeError:
                await ctx.send("Mod log is already disabled.")
            else:
                await modlog.set_modlog_channel(guild, None)
                await ctx.send("Mod log deactivated.")

    @modlogset.command(name="cases")
    @commands.guild_only()
    async def set_cases(self, ctx: commands.Context, action: str = None) -> None:
        """Enable or disable case creation for a mod action."""
        guild = ctx.guild

        if action is None:  # No args given
            casetypes = await modlog.get_all_casetypes(guild)
            await ctx.send_help()
            lines = []
            for ct in casetypes:
                enabled = "enabled" if await ct.is_enabled() else ("disabled")
                lines.append(f"{ct.name} : {enabled}")

            await ctx.send("Current settings:\n" + box("\n".join(lines)))
            return

        casetype = await modlog.get_casetype(action, guild)
        if not casetype:
            await ctx.send("That action is not registered.")
        else:
            enabled = await casetype.is_enabled()
            await casetype.set_enabled(not enabled)
            await ctx.send(f"Case creation for {action} actions is now {'disabled' if enabled else ('enabled')}.")

    @modlogset.command()
    @commands.guild_only()
    async def resetcases(self, ctx: commands.Context) -> None:
        """Reset all modlog cases in this server."""
        guild = ctx.guild
        await ctx.send("Are you sure you would like to reset all modlog cases in this server?" + " (yes/no)")
        try:
            pred = MessagePredicate.yes_or_no(ctx, user=ctx.author)
            await ctx.bot.wait_for("message", check=pred, timeout=30)
        except TimeoutError:
            await ctx.send("You took too long to respond.")
            return
        if pred.result:
            await modlog.reset_cases(guild)
            await ctx.send("Cases have been reset.")
        else:
            await ctx.send("No changes have been made.")

    @commands.command(hidden=True)
    @commands.guild_only()
    async def case(self, ctx: commands.Context, number: int) -> None:
        """Show the specified case."""
        try:
            case = await modlog.get_case(number, ctx.guild, self.bot)
        except RuntimeError:
            await ctx.send("That case does not exist for that server.")
            return
        else:
            if await ctx.embed_requested():
                await ctx.send(embed=await case.message_content(embed=True))
            else:
                message = ("{case}\n**Timestamp:** {timestamp}").format(case=await case.message_content(embed=False), timestamp=f"<t:{int(case.created_at)}>")
                await ctx.send(message)

    @commands.command(hidden=True)
    @commands.guild_only()
    async def casesfor(self, ctx: commands.Context, *, member: Union[discord.Member, int]):
        """Display cases for the specified member."""
        async with ctx.typing():
            try:
                if isinstance(member, int):
                    cases = await modlog.get_cases_for_member(bot=ctx.bot, guild=ctx.guild, member_id=member)
                else:
                    cases = await modlog.get_cases_for_member(bot=ctx.bot, guild=ctx.guild, member=member)
            except discord.NotFound:
                return await ctx.send("That user does not exist.")
            except discord.HTTPException:
                return await ctx.send("Something unexpected went wrong while fetching that user by ID.")

            if not cases:
                return await ctx.send("That user does not have any cases.")

            embed_requested = await ctx.embed_requested()
            if embed_requested:
                rendered_cases = [await case.message_content(embed=True) for case in cases]
            else:
                rendered_cases = []
                for case in cases:
                    message = ("{case}\n**Timestamp:** {timestamp}").format(
                        case=await case.message_content(embed=False),
                        timestamp=f"<t:{int(case.created_at)}>",
                    )
                    rendered_cases.append(message)

        await menu(ctx, rendered_cases, DEFAULT_CONTROLS)

    @commands.command(hidden=True)
    @commands.guild_only()
    async def listcases(self, ctx: commands.Context, *, member: Union[discord.Member, int]):
        """List cases for the specified member."""
        async with ctx.typing():
            try:
                if isinstance(member, int):
                    cases = await modlog.get_cases_for_member(bot=ctx.bot, guild=ctx.guild, member_id=member)
                else:
                    cases = await modlog.get_cases_for_member(bot=ctx.bot, guild=ctx.guild, member=member)
            except discord.NotFound:
                return await ctx.send("That user does not exist.")
            except discord.HTTPException:
                return await ctx.send("Something unexpected went wrong while fetching that user by ID.")
            if not cases:
                return await ctx.send("That user does not have any cases.")

            message = ""
            for case in cases:
                message += ("{case}\n**Timestamp:** {timestamp}\n\n").format(
                    case=await case.message_content(embed=False),
                    timestamp=f"<t:{int(case.created_at)}>",
                )
            rendered_cases = list(pagify(message, ["\n\n", "\n"], priority=True))
        await menu(ctx, rendered_cases, DEFAULT_CONTROLS)
