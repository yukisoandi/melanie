from __future__ import annotations

from .roleplay import Roleplay


def setup(bot) -> None:
    n = Roleplay(bot=bot)
    bot.add_cog(n)
