from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from melaniebot.core.bot import Melanie


async def setup(bot: Melanie) -> None:
    from melaniebot.core.dev_commands import Dev

    from .exe import ExecutionsTracker

    bot.remove_command("invite")
    bot.remove_command("reload")
    cog = ExecutionsTracker(bot)

    await cog.setup_db()

    bot.add_cog(cog)
