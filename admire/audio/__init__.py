from __future__ import annotations

from melaniebot.core.bot import Melanie

from .core import Audio


def setup(bot: Melanie) -> None:
    cog = Audio(bot)
    bot.add_cog(cog)
    cog.start_up_task()
