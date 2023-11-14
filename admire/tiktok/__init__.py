from __future__ import annotations

from .tiktok import TikTok


def setup(bot) -> None:
    bot.add_cog(TikTok(bot=bot))
