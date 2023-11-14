from __future__ import annotations

from .serverstats import ServerStats


def setup(bot) -> None:
    bot.add_cog(ServerStats(bot))
