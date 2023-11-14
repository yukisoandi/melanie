from __future__ import annotations


def setup(bot) -> None:
    from .fun import Fun

    bot.add_cog(Fun(bot))
