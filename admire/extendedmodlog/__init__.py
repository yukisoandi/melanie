from __future__ import annotations

import os


async def setup(bot) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "extendedmodlog.framework_settings")
    from .extendedmodlog import ExtendedModLog

    cog = ExtendedModLog(bot)
    await cog.initialize()
    bot.add_cog(cog)
