from __future__ import annotations

from abc import ABC

from melaniebot.core import Config
from melaniebot.core.bot import Melanie

from .api import API
from .cache import MemoryCache


class MixinMeta(ABC):
    """Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are
    defined in each mixin.

    Credit to

    """

    def __init__(self) -> None:
        self.bot: Melanie
        self.data: Config
        self.cache: MemoryCache
        self.api: API
