from __future__ import annotations

import logging

from audio.core.cog_utils import CompositeMetaClass
from melanie import log

from .cog import AudioEvents
from .dpy import DpyEvents
from .lavalink import LavalinkEvents
from .red import MelanieEvents


class Events(AudioEvents, DpyEvents, LavalinkEvents, MelanieEvents, metaclass=CompositeMetaClass):
    """Class joining all event subclasses."""
