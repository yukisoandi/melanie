from __future__ import annotations

from .conversions import Conversions


async def setup(bot) -> None:
    n = Conversions(bot)
    bot.add_cog(n)
