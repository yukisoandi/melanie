"""WelcomeCount - Welcomes users and keeps track of daily joins."""
from __future__ import annotations

from .welc import Welc


async def setup(bot) -> None:
    """Load welcomecount."""
    cog = Welc(bot=bot)
    await cog.init()
    bot.add_cog(cog)
