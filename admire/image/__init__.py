from __future__ import annotations

from image.image import Image


async def setup(bot) -> None:
    cog = Image(bot)
    bot.add_cog(cog)
