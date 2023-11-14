from __future__ import annotations

from typing import Union

import discord
from melaniebot.core import commands

from .starboard_entry import StarboardEntry


def _(x):
    return x


class StarboardExists(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> StarboardEntry:
        cog = ctx.cog
        guild = ctx.guild
        if guild.id not in cog.starboards:
            msg = "There are no starboards setup on this server!"
            raise commands.BadArgument(msg)
        try:
            starboard = cog.starboards[guild.id][argument.lower()]
        except KeyError as e:
            msg = f"There is no starboard named {argument}"
            raise commands.BadArgument(msg) from e

        return starboard


class RealEmoji(commands.EmojiConverter):
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
