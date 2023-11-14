from __future__ import annotations

from melaniebot.core.bot import Melanie

from .filter import Filter


async def setup(bot: Melanie) -> None:
    cog = Filter(bot)
    await cog.initialize()
    bot.add_cog(cog)
