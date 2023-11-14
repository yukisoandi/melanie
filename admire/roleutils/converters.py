from __future__ import annotations

from typing import Union

import discord
from discord.ext.commands import errors as dpy_errors
from melaniebot.core import commands
from rapidfuzz import process
from unidecode import unidecode

from melanie import BaseModel

from .utils import is_allowed_by_hierarchy, is_allowed_by_role_hierarchy


class MemberSettings(BaseModel):
    previous_roles: list = []
    removal_time: int = 0


class FuzzyRole(commands.RoleConverter):
    def __init__(self, response: bool = True) -> None:
        self.response = response
        super().__init__()

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        try:
            basic_role = await super().convert(ctx, argument)
        except (commands.BadArgument, commands.ConversionError, dpy_errors.BadArgument, dpy_errors.ConversionError):
            pass
        else:
            return basic_role
        guild = ctx.guild

        result = [(r[2], r[1]) for r in process.extract(argument, {r: unidecode(r.name) for r in guild.roles}, limit=None, score_cutoff=75)]
        if not result:
            raise commands.BadArgument(f"Role **{argument}** not found." if self.response else None)
        sorted_result = sorted(result, key=lambda r: r[1], reverse=True)
        return sorted_result[0][0]


class StrictRole(FuzzyRole):
    def __init__(self, response: bool = True, *, check_integrated: bool = False) -> None:
        self.response = response
        self.check_integrated = check_integrated
        super().__init__(response)

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        role = await super().convert(ctx, argument)
        if self.check_integrated and role.managed:
            raise commands.BadArgument(f"`{role}` is an integrated role and cannot be assigned." if self.response else None)
        (allowed, message) = await is_allowed_by_role_hierarchy(ctx.bot, ctx.me, ctx.author, role)
        if not allowed:
            raise commands.BadArgument(message if self.response else None)
        return role


class TouchableMember(commands.MemberConverter):
    def __init__(self, response: bool = True) -> None:
        self.response = response
        super().__init__()

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Member:
        try:
            member = await super().convert(ctx, argument)
        except (commands.BadArgument, commands.ConversionError, dpy_errors.BadArgument, dpy_errors.ConversionError) as e:
            raise commands.BadArgument(f"Member **{argument}** not found." if self.response else None) from e
        if not await is_allowed_by_hierarchy(ctx.bot, ctx.author, member):
            raise commands.BadArgument(f"You cannot do that since you aren't higher than {member} in hierarchy." if self.response else None)
        else:
            return member


class RealEmojiConverter(commands.EmojiConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.Emoji, str]:
        try:
            emoji = await super().convert(ctx, argument)
        except commands.BadArgument:
            try:
                await ctx.message.add_reaction(argument)
            except discord.HTTPException as e:
                raise commands.EmojiNotFound(argument) from e
            else:
                emoji = argument
        return emoji


class EmojiRole(StrictRole, RealEmojiConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> tuple[Union[discord.Emoji, str], discord.Role]:
        split = argument.split(";")
        if len(split) < 2:
            raise commands.BadArgument
        emoji = await RealEmojiConverter.convert(self, ctx, split[0])
        role = await StrictRole.convert(self, ctx, split[1])
        return (emoji, role)


class ObjectConverter(commands.IDConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Object:
        if match := self._get_id_match(argument):
            return discord.Object(int(match.group(0)))
        else:
            raise commands.BadArgument


class TargeterArgs(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> list[discord.Member]:
        members = await ctx.bot.get_cog("Targeter").args_to_list(ctx, argument)
        if not members:
            msg = f"No one was found with the given args.\nCheck out `{ctx.clean_prefix}target help` for an explanation."
            raise commands.BadArgument(msg)
        return members
