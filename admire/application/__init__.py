from __future__ import annotations

from .application import Application


def setup(bot) -> None:
    bot.add_cog(Application(bot))
