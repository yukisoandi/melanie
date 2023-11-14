from __future__ import annotations

from .away import Away


def setup(bot) -> None:
    bot.add_cog(Away(bot))
