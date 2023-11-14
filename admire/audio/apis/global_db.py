from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from typing import Optional, Union

import aiohttp
from lavalink.rest_api import LoadResult
from melaniebot.core import Config
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import Cog

from audio.audio_dataclasses import Query

_API_URL = "https://api.melaniebot.app/"


def _(x):
    return x


class GlobalCacheWrapper:
    def __init__(self, bot: Melanie, config: Config, session: aiohttp.ClientSession, cog: Union[Audio, Cog]) -> None:  # type: ignore
        # Place Holder for the Global Cache PR
        self.bot = bot
        self.config = config
        self.session = session
        self.api_key = None
        self._handshake_token = ""
        self.has_api_key = None
        self._token: Mapping[str, str] = {}
        self.cog = cog

    async def update_token(self, new_token: Mapping[str, str]) -> None:
        self._token = new_token
        await self.get_perms()

    async def _get_api_key(self) -> Optional[str]:
        # if not self._token:
        return None

    async def get_call(self, query: Optional[Query] = None) -> dict:
        return {}

    async def get_spotify(self, title: str, author: Optional[str]) -> dict:
        return {}

    async def post_call(self, llresponse: LoadResult, query: Optional[Query]) -> None:
        pass

    async def update_global(self, llresponse: LoadResult, query: Optional[Query] = None) -> None:
        await self.post_call(llresponse=llresponse, query=query)

    async def report_invalid(self, id: str) -> None:
        pass

    async def get_perms(self):
        global_api_user = copy(self.cog.global_api_user)
        await self._get_api_key()
        # global API is force-disabled right now
        return global_api_user
