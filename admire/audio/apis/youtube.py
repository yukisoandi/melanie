from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Optional

import aiohttp
import orjson
from melaniebot.core import Config
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import Cog

from melanie import log
from melanie.curl import SHARED_API_HEADERS, CurlRequest, get_curl
from melanie.models.yt_dlp_search import YoutubeSearchResults


class YouTubeWrapper:
    """Wrapper for the YouTube Data API."""

    def __init__(self, bot: Melanie, config: Config, session: aiohttp.ClientSession, cog: Cog) -> None:
        self.bot = bot
        self.config = config
        self.session = session
        self.api_key: Optional[str] = None
        self._token: Mapping[str, str] = {}
        self.cog = cog
        self.update_lock = asyncio.Lock()

    def __str__(self) -> str:
        return self.__class__.__name__

    async def update_token(self, new_token: Mapping[str, str]) -> None:
        self._token = new_token

    async def get_call(self, query: str) -> Optional[str]:
        """Make a Get call to youtube data api."""
        try:
            curl = get_curl()
            r = await curl.fetch(
                CurlRequest(
                    url="https://dev.melaniebot.net/api/youtube/search",
                    method="POST",
                    body=orjson.dumps({"query": query}),
                    headers=SHARED_API_HEADERS,
                ),
            )
            response = YoutubeSearchResults.parse_raw(r.body)
            return response.results[0].original_url if response.results else None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return log.warning("Error when fetching that track {}", e)
