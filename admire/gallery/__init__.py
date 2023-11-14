from __future__ import annotations

from .gallery import Gallery


def setup(bot) -> None:
    bot.add_cog(Gallery(bot))
