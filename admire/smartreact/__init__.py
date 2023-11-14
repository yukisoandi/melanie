from __future__ import annotations

from .smartreact import SmartReact


def setup(bot) -> None:
    bot.add_cog(SmartReact(bot))
