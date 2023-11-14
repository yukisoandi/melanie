from __future__ import annotations

from .botstatus import Botstatus


async def setup(bot) -> None:
    cog = Botstatus(bot)
    bot.add_cog(cog)
    cog.init()
