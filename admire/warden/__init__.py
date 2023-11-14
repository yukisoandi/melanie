from __future__ import annotations

from .warden import Warden


def setup(bot) -> None:
    n = Warden(bot)

    bot.add_cog(n)
