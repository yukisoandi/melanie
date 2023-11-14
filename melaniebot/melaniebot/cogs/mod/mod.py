from __future__ import annotations

import asyncio
from abc import ABC
from collections import defaultdict

from melanie import default_lock_cache

from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from .events import Events
from .kickban import KickBanMixin
from .names import ModInfo
from .settings import ModSettings
from .slowmode import Slowmode


def _(x):
    return x


__version__ = "1.2.0"


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """This allows the metaclass used for proper type detection to coexist with
    discord.py's metaclass.
    """


class Mod(ModSettings, Events, KickBanMixin, ModInfo, Slowmode, commands.Cog, metaclass=CompositeMetaClass):
    """Moderation tools."""

    default_global_settings = {"version": "", "track_all_names": True}

    default_guild_settings = {
        "mention_spam": {"ban": None, "kick": None, "warn": None, "strict": False},
        "delete_repeats": -1,
        "ignored": False,
        "respect_hierarchy": True,
        "delete_delay": -1,
        "reinvite_on_unban": True,
        "current_tempbans": [],
        "dm_on_kickban": True,
        "default_days": 0,
        "default_tempban_duration": 60 * 60 * 24,
        "track_nicknames": True,
    }

    default_channel_settings = {"ignored": False}

    default_member_settings = {"past_nicks": [], "perms_cache": {}, "banned_until": False}

    default_user_settings = {"past_names": []}

    def __init__(self, bot: Melanie) -> None:
        super().__init__()
        self.bot = bot
        self.locks = default_lock_cache()
        self.config = Config.get_conf(self, 4961522000, force_registration=True)
        self.config.register_global(**self.default_global_settings)
        self.config.register_guild(**self.default_guild_settings)
        self.config.register_channel(**self.default_channel_settings)
        self.config.register_member(**self.default_member_settings)
        self.config.register_user(**self.default_user_settings)
        self.cache: dict = {}
        self.tban_expiry_task = asyncio.create_task(self.tempban_expirations_task())
        self.last_case: dict = defaultdict(dict)

        self._ready = asyncio.Event()

    async def initialize(self):
        self._ready.set()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        await self._ready.wait()

    def cog_unload(self):
        self.tban_expiry_task.cancel()
