from __future__ import annotations

from melaniebot.core.bot import Melanie

from .lock import Lock


async def setup(bot: Melanie) -> None:
    bot.add_cog(Lock(bot))
