from __future__ import annotations

from .check import Check


def setup(bot) -> None:
    bot.add_cog(Check(bot))
