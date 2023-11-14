from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import discord

from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie


class MixinMeta(ABC):
    """Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are
    defined in each mixin.

    """

    def __init__(self, *_args) -> None:
        self.config: Config
        self.bot: Melanie
        self.cache: dict

    @staticmethod
    @abstractmethod
    async def _voice_perm_check(ctx: commands.Context, user_voice_state: Optional[discord.VoiceState], **perms: bool) -> bool:
        raise NotImplementedError
