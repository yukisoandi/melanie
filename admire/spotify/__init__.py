from __future__ import annotations


def setup(bot) -> None:
    from .spotify import Spotify

    cog = Spotify(bot)
    bot.add_cog(cog)
