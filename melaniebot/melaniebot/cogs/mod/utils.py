from __future__ import annotations

import discord

from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config


async def is_allowed_by_hierarchy(bot: Melanie, config: Config, guild: discord.Guild, mod: discord.Member, user: discord.Member):
    if not await config.guild(guild).respect_hierarchy():
        return True
    is_special = mod == guild.owner or await bot.is_owner(mod)
    return mod.top_role > user.top_role or is_special
