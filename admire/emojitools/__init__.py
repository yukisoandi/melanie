from __future__ import annotations

from .emojitools import EmojiTools


async def setup(bot) -> None:
    bot.add_cog(EmojiTools(bot))
