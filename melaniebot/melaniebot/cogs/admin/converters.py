from __future__ import annotations

import discord

from melaniebot.core import commands


def _(x):
    return x


class SelfRole(commands.Converter):
    async def convert(self, ctx: commands.Context, arg: str) -> discord.Role:
        admin = ctx.command.cog
        if admin is None:
            msg = "The Admin cog is not loaded."
            raise commands.BadArgument(msg)

        role_converter = commands.RoleConverter()
        role = await role_converter.convert(ctx, arg)

        selfroles = await admin.config.guild(ctx.guild).selfroles()

        if role.id not in selfroles:
            raise commands.BadArgument(_('The role "{role_name}" is not a valid selfrole.').format(role_name=role.name))
        return role
