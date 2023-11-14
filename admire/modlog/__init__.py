from __future__ import annotations

from melaniebot.core.bot import Melanie

from .modlog import ModLog


def setup(bot: Melanie) -> None:
    bot.add_cog(ModLog(bot))
