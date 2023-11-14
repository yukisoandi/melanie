from __future__ import annotations

from loguru import logger as log

from .say import Say


async def setup(bot) -> None:
    n = Say(bot)
    bot.add_cog(n)
    log.debug("Cog successfully loaded on the instance.")
