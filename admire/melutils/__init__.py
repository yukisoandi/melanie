from __future__ import annotations

from .mel_utils import Utilities


def setup(bot) -> None:
    cog = Utilities(bot)

    bot.add_cog(cog)
