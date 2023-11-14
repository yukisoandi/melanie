from __future__ import annotations


def setup(bot) -> None:
    from .userinfo import Userinfo

    bot.add_cog(Userinfo(bot=bot))
