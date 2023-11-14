from __future__ import annotations

from melaniebot.core.bot import Melanie

from .customhelp import CustomHelp


async def setup(bot: Melanie) -> None:
    cog = CustomHelp(bot)
    bot.add_cog(cog)
    # is this too costly? should I use a task rather?
    await cog._setup()
