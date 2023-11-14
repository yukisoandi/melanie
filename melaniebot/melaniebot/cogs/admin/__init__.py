from __future__ import annotations

from .admin import Admin


def setup(bot):
    bot.add_cog(Admin(bot))
