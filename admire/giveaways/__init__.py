from __future__ import annotations

from .giveaways import Giveaways


def setup(bot) -> None:
    bot.add_cog(Giveaways(bot))
