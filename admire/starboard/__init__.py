from __future__ import annotations

from .starboard import Starboard


def setup(bot) -> None:
    cog = Starboard(bot)
    bot.add_cog(cog)
