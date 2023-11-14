from __future__ import annotations

from melaniebot.core.bot import Melanie

from .purchases import Purchases


async def setup(bot: Melanie) -> None:
    bot.add_cog(Purchases(bot))
