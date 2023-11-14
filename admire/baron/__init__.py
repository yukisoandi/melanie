from __future__ import annotations

from melaniebot.core.bot import Melanie

from .baron import Baron


async def setup(bot: Melanie) -> None:
    cog = Baron(bot)
    bot.add_cog(cog)
