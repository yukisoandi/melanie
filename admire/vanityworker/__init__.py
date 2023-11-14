from __future__ import annotations

from .vanityworker import VanityWorker


async def setup(bot) -> None:
    from loguru import logger as log

    log.info("Starting vanity load")

    cog = VanityWorker(bot)

    bot.add_cog(cog)
    log.success("Vanity loaded ")
