# ABC Mixins


from __future__ import annotations

from abc import ABC
from collections import defaultdict

from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from dashboard.abc.mixin import DBMixin
from dashboard.abc.roles import DashboardRolesMixin
from dashboard.abc.settings import DashboardSettingsMixin
from dashboard.abc.webserver import DashboardWebserverMixin
from dashboard.baserpc import DashboardRPC

# Command Mixins

# RPC Mixins

THEME_COLORS = ["melanie", "primary", "blue", "green", "greener", "yellow"]


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """This allows the metaclass used for proper type detection to coexist with
    discord.py's metaclass.
    """


# Thanks to Flare for showing how to use group commands across multiple files.
# If this breaks, its his fault
class Dashboard(DashboardRolesMixin, DashboardWebserverMixin, DashboardSettingsMixin, DBMixin, commands.Cog, metaclass=CompositeMetaClass):
    __version__ = "0.1.8a"

    def __init__(self, bot: Melanie, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = bot

        self.config = Config.get_conf(self, identifier=473541068378341376)
        self.config.register_global(
            secret="[Not set]",
            redirect="http://127.0.0.1:42356/callback",
            clientid=0,
            blacklisted=[],
            disallowedperms=[],
            support="",
            defaultcolor="primary",
            meta={"title": "", "icon": "", "description": "", "color": ""},
        )
        self.config.register_guild(roles=[])
        self.configcache = defaultdict(self.cache_defaults)

        self.rpc = DashboardRPC(self)

    def cog_unload(self) -> None:
        self.configcache.clear()
        self.rpc.unload()

    def cache_defaults(self):
        return {"roles": []}

    async def initialize(self) -> None:
        config = await self.config.all_guilds()
        for k, v in config.items():
            self.configcache[k] = v
