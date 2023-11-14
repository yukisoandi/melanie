from __future__ import annotations

from abc import ABC, abstractmethod

from melaniebot.core import Config
from melaniebot.core.bot import Melanie


class MixinMeta(ABC):
    """Base class for well behaved type hint detection with composite class.
    Basically, to keep developers sane when not all attributes are defined in
    each mixin.

    Strategy borrowed from melaniebot.cogs.mutes.abc

    """

    config: Config
    bot: Melanie
    cache: dict

    def __init__(self, *_args) -> None:
        self.config: Config
        self.bot: Melanie
        self.cache: dict

    @abstractmethod
    async def initialize(self):
        ...
