from __future__ import annotations

from .channelstats import ChannelStats


async def setup(bot) -> None:
    n = ChannelStats(bot)
    bot.add_cog(n)
