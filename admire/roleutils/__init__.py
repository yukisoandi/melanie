from __future__ import annotations

from melaniebot.core.bot import Melanie

from .roleutils import RoleUtils


async def setup(bot: Melanie) -> None:
    role_utils = RoleUtils(bot)
    bot.add_cog(role_utils)
    await role_utils.initialize()
