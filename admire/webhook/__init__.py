from __future__ import annotations

from .webhook import Webhook


async def setup(bot) -> None:
    cog = Webhook(bot)
    await cog.initialize()
    bot.add_cog(cog)
