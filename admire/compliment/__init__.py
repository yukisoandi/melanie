from __future__ import annotations

from .compliment import Compliment


def setup(bot) -> None:
    n = Compliment(bot)
    bot.add_cog(n)
