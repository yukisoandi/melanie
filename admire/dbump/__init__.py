from __future__ import annotations

from .disboardreminder import DisboardReminder


async def setup(bot) -> None:
    cog = DisboardReminder(bot)
    await cog.initialize()
    bot.add_cog(cog)
