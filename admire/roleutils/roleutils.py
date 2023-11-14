from __future__ import annotations

from abc import ABC
from typing import Literal

from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config

from .converters import MemberSettings
from .reactroles import ReactRoles
from .roles import Roles

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """This allows the metaclass used for proper type detection to coexist with
    discord.py's metaclass.
    """


class RoleUtils(Roles, ReactRoles, commands.Cog, metaclass=CompositeMetaClass):
    """Useful role commands.

    Includes massroling, role targeting, and reaction roles.

    """

    __version__ = "1.3.7"

    def __init__(self, bot: Melanie, *_args) -> None:
        self.cache = {}
        self.bot = bot
        self.config = Config.get_conf(self, identifier=326235423452394523, force_registration=True)
        default_guild = {"reactroles": {"channels": [], "enabled": True}}
        self.config.register_guild(**default_guild)
        self.config.register_member(**MemberSettings().dict())
        default_guildmessage = {"reactroles": {"react_to_roleid": {}}}
        self.config.init_custom("GuildMessage", 2)
        self.config.register_custom("GuildMessage", **default_guildmessage)
        super().__init__(*_args)

    async def initialize(self) -> None:
        log.debug("RoleUtils initialize")
        await super().initialize()
