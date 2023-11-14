from __future__ import annotations

import discord as _discord

from melaniebot import VersionInfo, __version__, version_info

from .config import Config
from .utils.safety import warn_unsafe as _warn_unsafe

__all__ = ["Config", "__version__", "version_info", "VersionInfo"]

# Prevent discord PyNaCl missing warning
