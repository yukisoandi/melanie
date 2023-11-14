from __future__ import annotations


async def setup(bot) -> None:
    from .instagram import Instagram

    bot.add_cog(Instagram(bot))
