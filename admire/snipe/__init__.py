from __future__ import annotations

from .snipe import Snipe


def setup(bot) -> None:
    n = Snipe(bot)
    bot.add_cog(n)
