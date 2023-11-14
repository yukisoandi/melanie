from __future__ import annotations

from melaniebot.core.bot import Melanie

from .dashboard import Dashboard


async def setup(bot: Melanie) -> None:
    cog = Dashboard(bot)
    bot.add_cog(cog)
    await cog.initialize()
