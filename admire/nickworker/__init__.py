from __future__ import annotations


async def setup(bot) -> None:
    from .nickworker import NickNamerWorker

    cog = NickNamerWorker(bot)
    bot.add_cog(cog)
