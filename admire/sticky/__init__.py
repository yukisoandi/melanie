"""Sticky - Sticky messages to a channel."""
from __future__ import annotations

from .sticky import Sticky


def setup(bot) -> None:
    """Load Sticky."""
    bot.add_cog(Sticky(bot))
