from __future__ import annotations

from abc import ABC

from melaniebot.core import Config
from melaniebot.core.bot import Melanie


class MixinMeta(ABC):
    """Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are
    defined in each mixin.

    """

    def __init__(self, *_args) -> None:
        self.config: Config
        self.bot: Melanie
