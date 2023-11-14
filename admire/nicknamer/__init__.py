from __future__ import annotations


async def setup(bot) -> None:
    from .nicknamer import NickNamer

    cog = NickNamer(bot)
    bot.add_cog(cog)
