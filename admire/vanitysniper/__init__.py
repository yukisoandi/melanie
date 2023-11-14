from __future__ import annotations


async def setup(bot) -> None:
    from .vanitysniper import VanitySniper

    n = VanitySniper(bot)
    bot.add_cog(n)
