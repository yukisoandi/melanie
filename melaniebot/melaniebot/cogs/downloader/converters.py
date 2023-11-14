from __future__ import annotations

import discord

from melaniebot.core import commands

from .installable import InstalledModule


def _(x):
    return x


class InstalledCog(InstalledModule):
    @classmethod
    async def convert(cls, ctx: commands.Context, arg: str) -> InstalledModule:
        downloader = ctx.bot.get_cog("Downloader")
        if downloader is None:
            msg = "No Downloader cog found."
            raise commands.CommandError(msg)

        cog = discord.utils.get(await downloader.installed_cogs(), name=arg)
        if cog is None:
            msg = f"Cog `{arg}` is not installed."
            raise commands.BadArgument(msg)

        return cog
