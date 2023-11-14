from __future__ import annotations

from .anisearch import AniSearch


def setup(bot) -> None:
    n = AniSearch(bot)
    bot.add_cog(n)
