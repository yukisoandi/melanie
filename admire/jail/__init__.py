from __future__ import annotations

from melaniebot.core.bot import Melanie

from .jail import Jail


async def setup(bot: Melanie) -> None:
    bot.add_cog(Jail(bot))
