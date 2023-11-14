import asyncio
import contextlib
import time
from abc import ABC
from contextlib import suppress
from typing import Optional

import discord
import xxhash
from aiomisc.backoff import asyncretry
from discord import Embed, Message, TextChannel, errors
from loguru import logger as log

from melanie._redis import get_redis


class Dialog(ABC):
    """Abstract base class defining a general embed dialog interaction."""

    def __init__(self, *args, **kwargs) -> None:
        self._embed: Optional[Embed] = None
        self.message: Optional[Message] = None
        self.color: hex = kwargs.get("color") or kwargs.get("colour") or 0x000000

    async def _publish(self, channel: Optional[TextChannel], **kwargs) -> TextChannel:
        redis = get_redis()
        if channel is None and self.message is None:
            msg = "Missing argument. You need to specify a target channel or message."
            raise TypeError(msg)

        if channel is None:
            try:
                await self.message.edit(**kwargs)
            except errors.NotFound:
                self.message = None
        if self.message is None:
            self.message = await channel.send(**kwargs)

        with log.catch(exclude=asyncio.CancelledError):
            await redis.set(f"emitted_msg_stub:{xxhash.xxh32_hexdigest(str(self.message.id))}", str(time.time()), ex=320)
        return self.message.channel

    async def quit(self, text: str = None) -> None:
        """Quit the dialog.

        :param text: message text to display when dialog is closed
        :type text: :class:`str`, optional

        :rtype: ``None``

        """
        if text is None:
            with suppress(discord.HTTPException):
                await self.message.delete()
            self.message = None
        else:
            await self.display(text)
            with contextlib.suppress(errors.Forbidden):
                await self.message.clear_reactions()

    @asyncretry(max_tries=3, pause=0.1)
    async def update(self, text: str, color: hex = None, hide_author: bool = False, description: str = None, footer: tuple = None) -> None:
        """This will update the dialog embed.

        :param text: The new text.
        :param color: The new embed color.
        :param hide_author: True if you want to hide the embed author
            (default: ``False``).
        :rtype: ``None``

        """
        if color is None:
            color = self.color

        self._embed.colour = color
        self._embed.title = text
        self._embed.description = description

        if hide_author:
            self._embed.set_author(name="")

        if footer:
            self._embed.set_footer(text=footer[0], icon_url=footer[1])

        await self.display(embed=self._embed)

    @asyncretry(max_tries=3, pause=0.1)
    async def display(self, text: str = None, embed: Embed = None) -> None:
        """This will edit the dialog message.

        :param text: The new text.
        :param embed: The new embed.
        :rtype: ``None``

        """
        await self.message.edit(content=text, embed=embed)
