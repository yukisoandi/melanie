from __future__ import annotations

import struct
from typing import Final, Optional

import aiohttp
import regex as re

from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass

STREAM_TITLE: Final[re.Pattern] = re.compile(rb"StreamTitle='([^']*)';")


class ParsingUtilities(MixinMeta, metaclass=CompositeMetaClass):
    async def icyparser(self, url: str) -> Optional[str]:
        try:
            async with self.session.get(url, headers={"Icy-MetaData": "1"}) as resp:
                metaint = int(resp.headers["icy-metaint"])
                for _ in range(5):
                    await resp.content.readexactly(metaint)
                    metadata_length = struct.unpack("B", await resp.content.readexactly(1))[0] * 16
                    metadata = await resp.content.readexactly(metadata_length)
                    if not (m := re.search(STREAM_TITLE, metadata.rstrip(b"\0"))):
                        return None
                    if title := m.group(1):
                        title = title.decode("utf-8", errors="replace")
                        return title
        except (KeyError, aiohttp.ClientConnectionError, aiohttp.ClientResponseError):
            return None
