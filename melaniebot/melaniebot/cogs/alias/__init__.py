from __future__ import annotations

from melaniebot.core.bot import Melanie

from .alias import Alias


async def setup(bot):
    cog = Alias(bot)
    bot.add_cog(cog)
    cog.sync_init()
