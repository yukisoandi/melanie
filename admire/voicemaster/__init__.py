from __future__ import annotations

from .voicemaster import VoiceMaster


def setup(bot) -> None:
    n = VoiceMaster(bot)
    bot.add_cog(n)
