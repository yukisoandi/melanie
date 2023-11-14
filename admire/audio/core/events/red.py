from __future__ import annotations

from collections.abc import Mapping

from melaniebot.core import commands

from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass


def _(x):
    return x


class MelanieEvents(MixinMeta, metaclass=CompositeMetaClass):
    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Mapping[str, str]) -> None:
        if service_name == "youtube":
            await self.api_interface.youtube_api.update_token(api_tokens)
        elif service_name == "spotify":
            await self.api_interface.spotify_api.update_token(api_tokens)
        elif service_name == "audiodb":
            await self.api_interface.global_cache_api.update_token(api_tokens)
