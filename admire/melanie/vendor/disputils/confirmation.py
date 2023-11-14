import asyncio
import contextlib
from typing import Optional

import discord
from aiomisc.backoff import asyncretry
from discord.ext import commands

from .abc import Dialog


class Confirmation(Dialog):
    """Represents a message to let the user confirm a specific action."""

    def __init__(self, client: discord.Client, color: hex = 0x000000, message: discord.Message = None) -> None:
        super().__init__(color=color)

        self._client = client
        self.color = color
        self.emojis = {"✅": True, "❌": False}
        self._confirmed = None
        self.message = message
        self._embed: Optional[discord.Embed] = None

    @property
    def confirmed(self) -> bool:
        """Whether the user has confirmed the action."""
        return self._confirmed

    async def confirm(
        self,
        text: str,
        user: discord.User,
        channel: discord.TextChannel = None,
        hide_author: bool = False,
        description: str = None,
        footer: tuple = None,
        timeout: int = 20,
    ) -> bool or None:
        """Run the confirmation.

        :param text: The confirmation text.
        :type text: :class:`str`

        :param user: The user who has to confirm.
        :type user: :class:`discord.User`

        :param channel: The channel the message will be sent to. Must only be specified
            if ``self.message`` is None.
        :type channel: :class:`discord.TextChannel`, optional

        :param hide_author: Whether or not the ``user`` should be set as embed author.
        :type hide_author: bool, optional

        :type timeout: int
        :param timeout:
            Seconds to wait until stopping to listen for user interaction.

        :return: True when it's been confirmed, otherwise False. Will return None when a
            timeout occurs.
        :rtype: :class:`bool`, optional

        """
        emb = discord.Embed(title=text, color=self.color, description=description)
        if not hide_author:
            emb.set_author(name=str(user), icon_url=user.avatar_url)
        if footer:
            emb.set_footer(text=footer[0], icon_url=footer[1])

        self._embed = emb

        await self._publish(channel, embed=emb)
        msg = self.message

        @asyncretry(max_tries=4, pause=0.2)
        async def add_reaction(emoji):
            await msg.add_reaction(emoji)

        for emoji in self.emojis:
            await asyncio.sleep(0.01)
            await add_reaction(emoji)

        try:
            reaction = await self._client.wait_for(
                "raw_reaction_add",
                check=lambda r: (r.message_id == msg.id) and (r.user_id == user.id) and (str(r.emoji) in self.emojis),
                timeout=timeout,
            )
        except TimeoutError:
            self._confirmed = None
            return
        else:
            self._confirmed = self.emojis[str(reaction.emoji)]
            return self._confirmed
        finally:
            with contextlib.suppress(discord.Forbidden):
                await msg.clear_reactions()


class BotConfirmation(Confirmation):
    def __init__(self, ctx: commands.Context, color: hex = 0x000000, message: discord.Message = None) -> None:
        self._ctx = ctx

        super().__init__(ctx.bot, color, message)

    async def confirm(
        self,
        text: str,
        user: discord.User = None,
        channel: discord.TextChannel = None,
        hide_author: bool = False,
        description: str = None,
        footer: tuple = None,
        timeout: int = 20,
    ) -> bool or None:
        if user is None:
            user = self._ctx.author

        if self.message is None and channel is None:
            channel = self._ctx.channel

        return await super().confirm(text, user, channel, hide_author, description, footer, timeout)
