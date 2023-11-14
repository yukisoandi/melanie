from __future__ import annotations

from melaniebot.core.bot import Melanie

from .mod import Mod


async def setup(bot: Melanie):
    cog = Mod(bot)
    bot.add_cog(cog)
    await cog.initialize()
