from __future__ import annotations

from .vanity import Vanity


async def setup(bot) -> None:
    n = Vanity(bot)
    bot.add_cog(n)
