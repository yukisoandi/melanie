from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Optional, Union

import discord
from loguru import logger as log
from melaniebot import VersionInfo, version_info
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import AsyncIter


@dataclass
class FakePayload:
    """A fake payload object to utilize `_update_stars` method."""

    guild_id: int
    channel_id: int
    message_id: int
    user_id: int
    emoji: str
    event_type: str


@dataclass
class StarboardEntry:
    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.name: str = kwargs.get("name")
        self.guild: int = kwargs.get("guild")
        self.channel: int = kwargs.get("channel")
        self.emoji: str = kwargs.get("emoji")
        self.colour: str = kwargs.get("colour", "user")
        self.enabled: bool = kwargs.get("enabled", True)
        self.selfstar: bool = kwargs.get("selfstar", False)
        self.blacklist: list[int] = kwargs.get("blacklist", [])
        self.whitelist: list[int] = kwargs.get("whitelist", [])
        self.messages: dict[str, StarboardMessage] = kwargs.get("messages", {})
        self.starboarded_messages: dict[str, str] = kwargs.get("starboarded_messages", {})
        self.threshold: int = kwargs.get("threshold", 3)
        self.autostar: bool = kwargs.get("autostar", False)
        self.starred_messages: int = kwargs.get("starred_messages", 0)
        self.stars_added: int = kwargs.get("stars_added", 0)
        self.lock: asyncio.Lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"<Starboard guild={self.guild} name={self.name} emoji={self.emoji} enabled={self.enabled} threshold={self.threshold}>"

    def check_roles(self, member: Union[discord.Member, discord.User]) -> bool:
        """Checks if the user is allowed to add to the starboard Allows bot owner
        to always add messages for testing disallows users from adding their
        own messages.

        Parameters
        ----------
            member: Union[discord.Member, discord.User]
                The member object which added the reaction for this starboard.

        Returns
        -------
            bool
                Whether or not this member is allowed to utilize this starboard.

        """
        if not isinstance(member, discord.Member):
            # this will account for non-members reactions and still count
            # for the starboard count
            return True
        user_roles = {role.id for role in member.roles}
        if self.whitelist:
            return any(role in user_roles for role in self.whitelist)
            # Since we'd normally return True
            # if there is a whitelist we want to ensure only whitelisted
            # roles can starboard something
            # Since we'd normally return True
            # if there is a whitelist we want to ensure only whitelisted
            # roles can starboard something
        elif self.blacklist:
            for role in self.blacklist:
                if role in user_roles:
                    return False

        return True

    def check_channel(self, bot: Melanie, channel: discord.TextChannel) -> bool:
        """Checks if the channel is allowed to track starboard messages.

        Parameters
        ----------
            bot: Melanie
                The bot object
            channel: discord.TextChannel
                The channel we want to verify we're allowed to post in

        Returns
        -------
            bool
                Whether or not the channel we got a "star" in we're allowed
                to repost.

        """
        guild = bot.get_guild(self.guild)
        if channel.is_nsfw() and not guild.get_channel(self.channel).is_nsfw():
            return False
        if self.whitelist:
            return True if channel.id in self.whitelist else bool(channel.category_id and channel.category_id in self.whitelist)

        if channel.id in self.blacklist:
            return False
        return bool(not channel.category_id or channel.category_id not in self.blacklist)

    async def to_json(self) -> dict:
        return {
            "name": self.name,
            "guild": self.guild,
            "enabled": self.enabled,
            "channel": self.channel,
            "emoji": self.emoji,
            "colour": self.colour,
            "selfstar": self.selfstar,
            "blacklist": self.blacklist,
            "whitelist": self.whitelist,
            "messages": {k: m.to_json() async for k, m in AsyncIter(self.messages.items(), steps=1)},
            "starboarded_messages": self.starboarded_messages,
            "threshold": self.threshold,
            "autostar": self.autostar,
            "starred_messages": self.starred_messages,
            "stars_added": self.stars_added,
        }

    @classmethod
    async def from_json(cls, data: dict, guild_id: Optional[int]) -> StarboardEntry:
        messages = data.get("messages", {})
        guild = data.get("guild", guild_id)
        if guild is None and guild_id is not None:
            guild = guild_id
        starboarded_messages = data.get("starboarded_messages", {})
        new_messages = {}
        if isinstance(messages, list):
            async for message_data in AsyncIter(messages, steps=1):
                message_obj = StarboardMessage.from_json(message_data, guild)
                if not message_obj.guild:
                    message_obj.guild = guild
                key = f"{message_obj.original_channel}-{message_obj.original_message}"
                new_messages[key] = message_obj
        else:
            async for key, value in AsyncIter(messages.items()):
                msg = StarboardMessage.from_json(value, guild)
                new_messages[key] = msg
        messages = new_messages
        if not starboarded_messages:
            async for _message_ids, obj in AsyncIter(messages.items()):
                key = f"{obj.new_channel}-{obj.new_message}"
                starboarded_messages[key] = f"{obj.original_channel}-{obj.original_message}"
        starred_messages = data.get("starred_messages", len(starboarded_messages))
        stars_added = data.get("stars_added", 0)
        if not stars_added:
            async for _message_id, message in AsyncIter(messages.items(), steps=1):
                stars_added += len(message.reactions)
        blacklist = data.get("blacklist", [])
        whitelist = data.get("whitelist", [])
        if data.get("blacklist_channel") or data.get("blacklist_role"):
            log.debug("Converting blacklist")
            blacklist += data.get("blacklist_channel", [])
            blacklist += data.get("blacklist_role", [])
        if data.get("whitelist_channel") or data.get("whitelist_role"):
            log.debug("Converting whitelist")
            whitelist += data.get("whitelist_channel", [])
            whitelist += data.get("whitelist_role", [])
        return cls(
            name=data.get("name"),
            guild=guild,
            channel=data.get("channel"),
            emoji=data.get("emoji"),
            colour=data.get("colour", "user"),
            enabled=data.get("enabled"),
            selfstar=data.get("selfstar", False),
            blacklist=blacklist,
            whitelist=whitelist,
            messages=messages,
            threshold=data.get("threshold"),
            autostar=data.get("autostar", False),
            starboarded_messages=starboarded_messages,
            starred_messages=starred_messages,
            stars_added=stars_added,
        )


@dataclass
class StarboardMessage:
    """A class to hold message objects pertaining To starboarded messages
    including the original message ID, and the starboard message ID as well as
    a list of users who have added their "vote".
    """

    def __init__(self, **kwargs) -> None:
        self.guild: int = kwargs.get("guild")
        self.original_message: int = kwargs.get("original_message", 0)
        self.original_channel: int = kwargs.get("original_channel", 0)
        self.new_message: Optional[int] = kwargs.get("new_message")
        self.new_channel: Optional[int] = kwargs.get("new_channel")
        self.author: int = kwargs.get("author", 0)
        self.reactions: list[int] = kwargs.get("reactions", [])

    def __repr__(self) -> str:
        return f"<StarboardMessage author={self.author} guild={self.guild} count={len(self.reactions)} original_channel={self.original_channel} original_message={self.original_message} new_channel={self.new_channel} new_message={self.new_message}>"

    async def delete(self, star_channel: discord.TextChannel) -> None:
        if self.new_message is None:
            return
        try:
            if version_info >= VersionInfo.from_str("3.4.6"):
                message_edit = star_channel.get_partial_message(self.new_message)
            else:
                message_edit = await star_channel.fetch_message(self.new_message)
            self.new_message = None
            self.new_channel = None
            await message_edit.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            return

    async def edit(self, star_channel: discord.TextChannel, content: str) -> None:
        if self.new_message is None:
            return
        try:
            if version_info >= VersionInfo.from_str("3.4.6"):
                message_edit = star_channel.get_partial_message(self.new_message)
            else:
                message_edit = await star_channel.fetch_message(self.new_message)
            await message_edit.edit(content=content)
        except (discord.errors.NotFound, discord.errors.Forbidden):
            return

    async def update_count(self, bot: Melanie, starboard: StarboardEntry, remove: Optional[int]) -> None:
        """This function can pull the most accurate reaction info from a
        starboarded message However it takes at least 2 API calls which can be
        expensive. I am leaving This here for future potential needs but we
        should instead rely on our listener to keep track of reactions
        added/removed.

        Parameters
        ----------
            bot: Melanie
                The bot object used for bot.get_guild
            starbaord: StarboardEntry
                The starboard object which contains this message entry
            remove: Optional[int]
                This was used to represent a user who removed their reaction.

        Returns
        -------
            MessageEntry
                Returns itself although since this is handled in memory is not required.

        """
        guild = bot.get_guild(self.guild)
        orig_channel = guild.get_channel(self.original_channel)
        new_channel = guild.get_channel(self.new_channel)
        orig_reaction = []
        if orig_channel:
            with contextlib.suppress(discord.HTTPException):
                orig_msg = await orig_channel.fetch_message(self.original_message)
                orig_reaction = [r for r in orig_msg.reactions if str(r.emoji) == str(starboard.emoji)]
        new_reaction = []
        if new_channel:
            with contextlib.suppress(discord.HTTPException):
                new_msg = await new_channel.fetch_message(self.new_message)
                new_reaction = [r for r in new_msg.reactions if str(r.emoji) == str(starboard.emoji)]
        reactions = orig_reaction + new_reaction
        for reaction in reactions:
            async for user in reaction.users():
                if not starboard.check_roles(user):
                    continue
                if not starboard.selfstar and user.id == orig_msg.author.id:
                    continue
                if user.id not in self.reactions and not user.bot:
                    self.reactions.append(user.id)
        if remove and remove in self.reactions:
            self.reactions.remove(remove)
        self.reactions = list(set(self.reactions))
        return self

    def to_json(self) -> dict[str, Union[list[int], int, None]]:
        return {
            "guild": self.guild,
            "original_message": self.original_message,
            "original_channel": self.original_channel,
            "new_message": self.new_message,
            "new_channel": self.new_channel,
            "author": self.author,
            "reactions": self.reactions,
        }

    @classmethod
    def from_json(cls, data: dict[str, Union[list[int], int, None]], guild_id: Optional[int]) -> StarboardMessage:
        return cls(
            guild=data.get("guild", guild_id),
            original_message=data.get("original_message"),
            original_channel=data.get("original_channel"),
            new_message=data.get("new_message"),
            new_channel=data.get("new_channel"),
            author=data.get("author"),
            reactions=data.get("reactions", []),
        )
