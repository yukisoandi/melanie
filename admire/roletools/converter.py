from __future__ import annotations

import contextlib

import discord
import regex as re
from discord.ext.commands import BadArgument, Converter
from melaniebot.core import commands


def _(x):
    return x


_id_regex = re.compile(r"([0-9]{15,21})$")
_mention_regex = re.compile(r"<@!?([0-9]{15,21})>$")


class RawUserIds(Converter):
    #
    async def convert(self, ctx: commands.Context, argument: str) -> int:
        # This is for the hackban and unban commands, where we receive IDs that
        # are most likely not in the guild.
        # Mentions are supported, but most likely won't ever be in cache.

        if match := _id_regex.match(argument) or _mention_regex.match(argument):
            return int(match.group(1))

        msg = f"{argument} doesn't look like a valid user ID."
        raise BadArgument(msg)


class RoleHierarchyConverter(commands.RoleConverter):
    """Similar to d.py's RoleConverter but only returns if we have already passed
    our hierarchy checks.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        if not ctx.me.guild_permissions.manage_roles:
            msg = "I require manage roles permission to use this command."
            raise BadArgument(msg)
        try:
            role = await commands.RoleConverter().convert(ctx, argument)
        except commands.BadArgument:
            raise
        else:
            if getattr(role, "is_bot_managed", None) and role.is_bot_managed():
                msg = f"The {role.mention} role is a bot integration role and cannot be assigned or removed."
                raise BadArgument(msg)
            if getattr(role, "is_integration", None) and role.is_integration():
                raise BadArgument(("The {role} role is an integration role and cannot be assigned or removed.").fromat(role=role.mention))
            if getattr(role, "is_premium_subscriber", None) and role.is_premium_subscriber():
                msg = f"The {role.mention} role is a premium subscriber role and can only be assigned or removed by Nitro boosting the server."
                raise BadArgument(msg)
            if role >= ctx.me.top_role:
                msg = f"The {role.mention} role is higher than my highest role in the discord hierarchy."
                raise BadArgument(msg)
            if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
                msg = f"The {role.mention} role is higher than your highest role in the discord hierarchy."
                raise BadArgument(msg)
        return role


class SelfRoleConverter(commands.RoleConverter):
    """Converts a partial role name into a role object that can actually be
    applied.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        if not ctx.me.guild_permissions.manage_roles:
            msg = "I require manage roles permission to use this command."
            raise BadArgument(msg)
        role = None
        try:
            role = await commands.RoleConverter().convert(ctx, argument)
        except commands.BadArgument:
            for roles in ctx.guild.roles:
                if roles.name.lower() == argument.lower():
                    role = roles
        if role is None:
            raise commands.RoleNotFound(argument)
        if role.is_bot_managed():
            msg = f"The {role.mention} role is a bot integration role and cannot be assigned or removed."
            raise BadArgument(msg)
        if role.is_integration():
            raise BadArgument(("The {role} role is an integration role and cannot be assigned or removed.").fromat(role=role.mention))
        if role.is_premium_subscriber():
            msg = f"The {role.mention} role is a premium subscriber role and can only be assigned or removed by Nitro boosting the server."
            raise BadArgument(msg)
        if role >= ctx.me.top_role:
            msg = f"The {role.mention} role is higher than my highest role in the discord hierarchy."
            raise BadArgument(msg)
        return role


class RoleEmojiConverter(Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> tuple[discord.Role, str]:
        arg_split = re.split(r";|,|\||-", argument)
        try:
            role, emoji = arg_split
        except Exception as e:
            msg = "Role Emoji must be a role followed by an emoji separated by either `;`, `,`, `|`, or `-`."
            raise BadArgument(msg) from e

        custom_emoji = None
        with contextlib.suppress(commands.BadArgument):
            custom_emoji = await commands.PartialEmojiConverter().convert(ctx, emoji.strip())

        if not custom_emoji:
            try:
                await ctx.message.add_reaction(str(emoji.strip()))
                custom_emoji = emoji
            except discord.errors.HTTPException as exc:
                msg = "That does not look like a valid emoji."
                raise BadArgument(msg) from exc
        try:
            role = await RoleHierarchyConverter().convert(ctx, role.strip())
        except commands.BadArgument:
            raise
        return role, custom_emoji
