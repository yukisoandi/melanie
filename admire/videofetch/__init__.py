from __future__ import annotations

from melaniebot.core.bot import Melanie

from .videofetch import VideoFetch


async def setup(bot: Melanie) -> None:
    bot.add_cog(VideoFetch(bot))
