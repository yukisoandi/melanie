"""Package for RemindMe cog."""
from __future__ import annotations

from melaniebot.core.bot import Melanie

from remindme.remindme import RemindMe


async def setup(bot: Melanie) -> None:
    """Load RemindMe cog."""
    cog = RemindMe(bot)
    await cog.initialize()
    bot.add_cog(cog)
