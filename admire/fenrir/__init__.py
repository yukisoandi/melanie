from __future__ import annotations

from .fenrir import Fenrir


def setup(bot) -> None:
    bot.add_cog(Fenrir(bot))
