from __future__ import annotations

import logging

from audio.core.cog_utils import CompositeMetaClass
from melanie import log

from .lavalink import LavalinkTasks
from .player import PlayerTasks
from .startup import StartUpTasks


class Tasks(LavalinkTasks, PlayerTasks, StartUpTasks, metaclass=CompositeMetaClass):
    """Class joining all task subclasses."""
