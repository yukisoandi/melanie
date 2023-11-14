from __future__ import annotations

from .seen import Seen


async def setup(bot) -> None:
    cog = Seen(bot)

    bot.add_cog(cog)
