from __future__ import annotations

from .abc import MixinMeta  # type: ignore


def _(x):
    return x


class ModSettings(MixinMeta):
    """This is a mixin for the mod cog containing all settings commands."""
