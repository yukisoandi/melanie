from __future__ import annotations

from melaniebot.core.bot import Melanie

from .antinuke import AntiNuke


async def setup(bot: Melanie) -> None:
    bot.add_cog(AntiNuke(bot))
