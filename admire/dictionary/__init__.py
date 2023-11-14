from __future__ import annotations

from .dictionary import Dictionary
from .helpers import get_soup_object


def setup(bot) -> None:
    bot.add_cog(Dictionary(bot))
