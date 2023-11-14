from __future__ import annotations

from .tarotreading import TarotReading


def setup(bot) -> None:
    bot.add_cog(TarotReading(bot))
