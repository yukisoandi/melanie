from __future__ import annotations

from melaniebot.core.bot import Melanie

from .nitrorole import NitroRole


async def setup(bot: Melanie) -> None:
    bot.add_cog(NitroRole(bot))
