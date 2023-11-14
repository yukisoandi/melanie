from __future__ import annotations

from .fixedfloat import FixedFloat


async def setup(bot) -> None:
    n = FixedFloat(bot)
    bot.add_cog(n)
