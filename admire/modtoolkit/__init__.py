from __future__ import annotations

from .modtoolkit import Modtoolkit


async def setup(bot) -> None:
    cog = Modtoolkit(bot)
    bot.add_cog(cog)
