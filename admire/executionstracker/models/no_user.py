from __future__ import annotations

import discord

from modsystem.api import BadArgument, MemberConverter, id_pattern

from ..exe import FakeRole
from ..helpers import UnavailableMember


def _(x):
    return x


class UnavailableMember(discord.abc.User, discord.abc.Messageable):
    """A class that reproduces the behaviour of a discord.Member instance, except
    the member is not in the guild.

    This is used to prevent calling bot.fetch_info which has a very high
    cooldown.

    """

    def __init__(self, bot, state, user_id: int) -> None:
        self.bot = bot
        self._state = state
        self.id = user_id
        self.top_role = FakeRole()

    @classmethod
    def _check_id(cls, member_id):
        if not id_pattern.match(member_id):
            msg = f"You provided an invalid ID: {member_id}"
            raise ValueError(msg)
        return int(member_id)

    @classmethod
    async def convert(cls, ctx, text):
        try:
            member = await MemberConverter().convert(ctx, text)
        except BadArgument:
            pass
        else:
            return member
        try:
            member_id = cls._check_id(text)
        except ValueError as e:
            msg = "The given member cannot be found.\nIf you're trying to hackban, the user ID is not valid."
            raise BadArgument(msg) from e

        return cls(ctx.bot, ctx._state, member_id)

    @property
    def name(self) -> str:
        return "Unknown"

    @property
    def display_name(self) -> str:
        return "Unknown"

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def avatar_url(self) -> str:
        return ""

    def __str__(self) -> str:
        return "Unknown#0000"

    # the 3 following functions were copied from the discord.User class, credit to Rapptz
    #

    @property
    def dm_channel(self):
        """Optional[:class:`DMChannel`]: Returns the channel associated with this user if it exists.
        If this returns ``None``, you can create a DM channel by calling the
        :meth:`create_dm` coroutine function.
        """
        return self._state._get_private_channel_by_user(self.id)

    async def create_dm(self):
        """Creates a :class:`DMChannel` with this user.

        This should be rarely called, as this is done transparently for
        most people.

        """
        found = self.dm_channel
        if found is not None:
            return found

        state = self._state
        data = await state.http.start_private_message(self.id)
        return state.add_dm_channel(data)

    async def _get_channel(self):
        return await self.create_dm()
