from __future__ import annotations

from .roletools import RoleTools


async def setup(bot) -> None:
    cog = RoleTools(bot)
    bot.add_cog(cog)
    await cog.initalize()
