from __future__ import annotations

from .colorme import ColorMe


async def setup(bot) -> None:
    cog = ColorMe(bot)

    bot.add_cog(cog)
    await cog.init()
