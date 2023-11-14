from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional, Union

import discord
import regex as re
from discord.ext.commands import Context as DPYContext
from loguru import logger as log
from melanie import log

from melaniebot.core.utils.chat_formatting import box
from melaniebot.core.utils.predicates import MessagePredicate

from .requires import PermState

if TYPE_CHECKING:
    from melaniebot.core.bot import Melanie

    from .commands import Command

TICK = "\N{WHITE HEAVY CHECK MARK}"

__all__ = ["Context", "GuildContext", "DMContext"]


class Context(DPYContext):
    """Command invocation context for Melanie.

    All context passed into commands will be of this type.

    This class inherits from `discord.ext.commands.Context`.

    Attributes
    ----------
    assume_yes: bool
        Whether or not interactive checks should
        be skipped and assumed to be confirmed.

        This is intended for allowing automation of tasks.

        An example of this would be scheduled commands
        not requiring interaction if the cog developer
        checks this value prior to confirming something interactively.

        Depending on the potential impact of a command,
        it may still be appropriate not to use this setting.
    permission_state: PermState
        The permission state the current context is in.

    """

    command: Command
    invoked_subcommand: Optional[Command]
    bot: Melanie
    guild: discord.Guild
    author: Union[discord.Member, discord.User]
    me: Union[discord.Member, discord.User]
    message: discord.Message | None
    channel: discord.TextChannel | discord.VoiceChannel

    def __init__(self, **attrs) -> None:
        self.assume_yes = attrs.pop("assume_yes", False)
        super().__init__(**attrs)
        self.permission_state: PermState = PermState.NORMAL

    @property
    def bot_owner(self) -> bool:
        return self.author.id in self.bot.owner_ids

    async def send(self, content=None, **kwargs) -> discord.Message:
        """Sends a message to the destination with the content given.

        This acts the same as `discord.ext.commands.Context.send`, with
        one added keyword argument as detailed below in *Other Parameters*.

        Parameters
        ----------
        content : str
            The content of the message to send.

        Other Parameters
        ----------------
        filter : callable (`str`) -> `str`, optional
            A function which is used to filter the ``content`` before
            it is sent.
            This must take a single `str` as an argument, and return
            the processed `str`. When `None` is passed, ``content`` won't be touched.
            Defaults to `None`.
        **kwargs
            See `discord.ext.commands.Context.send`.

        Returns
        -------
        discord.Message
            The message that was sent.

        """
        _filter = kwargs.pop("filter", None)
        _reply = kwargs.pop("reply", False)
        _ping = kwargs.pop("ping", False)
        await asyncio.sleep(0.001)

        if _filter and content:
            content = _filter(str(content))
        if _reply:
            reference = self.message.to_reference(fail_if_not_exists=False)
            try:
                msg = await super().send(content=content, mention_author=_ping, reference=reference, *kwargs)
                return msg
            except discord.HTTPException as e:
                log.warning("Reply error: {}", e)

        msg = await super().send(content=content, **kwargs)
        return msg

    async def send_help(self, command=None):
        """Send the command help message."""
        # This allows people to manually use this similarly
        # to the upstream d.py version, while retaining our use.
        command = command or self.command
        await self.bot.send_help_for(self, command)

    async def tick(self, *, message: Optional[str] = None) -> bool:
        """Add a tick reaction to the command message.

        Keyword Arguments:
        -----------------
        message : str, optional
            The message to send if adding the reaction doesn't succeed.

        Returns:
        -------
        bool
            :code:`True` if adding the reaction succeeded.

        """
        return await self.react_quietly(TICK, message=message)

    async def react_quietly(self, reaction: Union[discord.Emoji, discord.Reaction, discord.PartialEmoji, str], *, message: Optional[str] = None) -> bool:
        """Adds a reaction to the command message.

        Parameters
        ----------
        reaction : Union[discord.Emoji, discord.Reaction, discord.PartialEmoji, str]
            The emoji to react with.

        Keyword Arguments
        -----------------
        message : str, optional
            The message to send if adding the reaction doesn't succeed.

        Returns
        -------
        bool
            :code:`True` if adding the reaction succeeded.

        """
        try:
            if not self.channel.permissions_for(self.me).add_reactions:
                raise RuntimeError
            await self.message.add_reaction(reaction)
        except (RuntimeError, discord.HTTPException):
            if message is not None:
                await self.send(message)
            return False
        else:
            return True

    async def send_interactive(self, messages: Iterable[str], box_lang: str = None, timeout: int = 15) -> list[discord.Message]:
        """Send multiple messages interactively.

        The user will be prompted for whether or not they would like to view
        the next message, one at a time. They will also be notified of how
        many messages are remaining on each prompt.

        Parameters
        ----------
        messages : `iterable` of `str`
            The messages to send.
        box_lang : str
            If specified, each message will be contained within a codeblock of
            this language.
        timeout : int
            How long the user has to respond to the prompt before it times out.
            After timing out, the bot deletes its prompt message.

        """
        messages = tuple(messages)
        ret = []

        for idx, page in enumerate(messages, 1):
            if box_lang is None:
                msg = await self.send(page)
            else:
                msg = await self.send(box(page, lang=box_lang))
            ret.append(msg)
            n_remaining = len(messages) - idx
            if n_remaining > 0:
                if n_remaining == 1:
                    plural = ""
                    is_are = "is"
                else:
                    plural = "s"
                    is_are = "are"
                query = await self.send(f"There {is_are} still {n_remaining} message{plural} remaining. Type `more` to continue.")

                try:
                    resp = await self.bot.wait_for("message", check=MessagePredicate.lower_equal_to("more", self), timeout=timeout)
                except asyncio.TimeoutError:
                    with contextlib.suppress(discord.HTTPException):
                        await query.delete()
                    break
                else:
                    try:
                        await self.channel.delete_messages((query, resp))
                    except (discord.HTTPException, AttributeError):
                        # In case the bot can't delete other users' messages,
                        # or is not a bot account
                        # or channel is a DM
                        with contextlib.suppress(discord.HTTPException):
                            await query.delete()
        return ret

    async def embed_colour(self):
        """Helper function to get the colour for an embed.

        Returns
        -------
        discord.Colour:
            The colour to be used

        """
        return await self.bot.get_embed_color(self)

    @property
    def embed_color(self):
        # Rather than double awaiting.
        return self.embed_colour

    async def embed_requested(self):
        """Simple helper to call bot.embed_requested with logic around if embed
        permissions are available.

        Returns
        -------
        bool:
            :code:`True` if an embed is requested

        """
        if self.guild and not self.channel.permissions_for(self.guild.me).embed_links:
            return False
        return await self.bot.embed_requested(self.channel, self.author, command=self.command)

    async def maybe_send_embed(self, message: str) -> discord.Message:
        """Simple helper to send a simple message to context without manually
        checking ctx.embed_requested This should only be used for simple
        messages.

        Parameters
        ----------
        message: `str`
            The string to send

        Returns
        -------
        discord.Message:
            the message which was sent

        Raises
        ------
        discord.Forbidden
            see `discord.abc.Messageable.send`
        discord.HTTPException
            see `discord.abc.Messageable.send`
        ValueError
            when the message's length is not between 1 and 2000 characters.

        """
        if not message or len(message) > 2000:
            msg = "Message length must be between 1 and 2000"
            raise ValueError(msg)
        if await self.embed_requested():
            return await self.send(embed=discord.Embed(description=message, color=(await self.embed_colour())))
        else:
            return await self.send(message, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False))

    @property
    def clean_prefix(self) -> str:
        """str: The command prefix, but with a sanitized version of the bot's mention if it was used as prefix.
        This can be used in a context where discord user mentions might not render properly.
        """
        me = self.me
        pattern = re.compile(rf"<@!?{me.id}>")
        return pattern.sub(f"@{me.display_name}".replace("\\", r"\\"), self.prefix)

    @property
    def me(self) -> Union[discord.ClientUser, discord.Member]:
        """discord.abc.User: The bot member or user object.

        If the context is DM, this will be a `discord.User` object.
        """
        return self.guild.me if self.guild is not None else self.bot.user


GuildContext = Context
DMContext = Context
