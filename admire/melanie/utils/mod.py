from __future__ import annotations

import asyncio
import contextlib
import warnings
from collections.abc import Iterable
from datetime import timedelta
from typing import Union

import discord

try:
    from melaniebot.core import Config
    from melaniebot.core.bot import Melanie
    from melaniebot.core.commands import Context
except ModuleNotFoundError:
    Config = None
    Melanie = None

    Context = None


async def mass_purge(messages: list[discord.Message], channel: discord.TextChannel) -> None:
    """Bulk delete messages from a channel.

    If more than 100 messages are supplied, the bot will delete 100 messages at
    a time, sleeping between each action.

    Note
    ----
    Messages must not be older than 14 days, and the bot must not be a user
    account.

    Parameters
    ----------
    messages : `list` of `discord.Message`
        The messages to bulk delete.
    channel : discord.TextChannel
        The channel to delete messages from.

    Raises
    ------
    discord.Forbidden
        You do not have proper permissions to delete the messages or you`re not
        using a bot account.
    discord.HTTPException
        Deleting the messages failed.

    """
    while messages:
        # discord.NotFound can be raised when `len(messages) == 1` and the message does not exist.
        # As a result of this obscure behavior, this error needs to be caught just in case.
        with contextlib.suppress(discord.errors.HTTPException):
            await channel.delete_messages(messages[:100])
        messages = messages[100:]
        await asyncio.sleep(1.5)


async def slow_deletion(messages: Iterable[discord.Message]) -> None:
    """Delete a list of messages one at a time.

    Any exceptions raised when trying to delete the message will be silenced.

    Parameters
    ----------
    messages : `iterable` of `discord.Message`
        The messages to delete.

    """
    for message in messages:
        with contextlib.suppress(discord.HTTPException):
            await message.delete()


def get_audit_reason(author: discord.Member, reason: str = None, *, shorten: bool = False) -> str:
    """Construct a reason to appear in the audit log.

    Parameters
    ----------
    author : discord.Member
        The author behind the audit log action.
    reason : str
        The reason behind the audit log action.
    shorten : bool
        When set to ``True``, the returned audit reason string will be
        shortened to fit the max length allowed by Discord audit logs.

    Returns
    -------
    str
        The formatted audit log reason.

    """
    audit_reason = f"Action requested by {author} (ID {author.id}). Reason: {reason}" if reason else f"Action requested by {author} (ID {author.id})."

    if shorten and len(audit_reason) > 512:
        audit_reason = f"{audit_reason[:509]}..."
    return audit_reason


async def is_allowed_by_hierarchy(bot: Melanie, settings: Config, guild: discord.Guild, mod: discord.Member, user: discord.Member):
    warnings.warn(
        "`is_allowed_by_hierarchy()` is deprecated since Melanie 3.4.1 and will be removed in the first minor release after 2020-11-31.",
        DeprecationWarning,
        stacklevel=2,
    )
    if not await settings.guild(guild).respect_hierarchy():
        return True
    is_special = mod == guild.owner or await bot.is_owner(mod)
    return mod.top_role > user.top_role or is_special


async def is_mod_or_superior(bot: Melanie, obj: Union[discord.Message, discord.Member, discord.Role]) -> bool:
    """Check if an object has mod or superior permissions.

    If a message is passed, its author's permissions are checked. If a role is
    passed, it simply checks if it is one of either the admin or mod roles.

    Parameters
    ----------
    bot : melaniebot.core.bot.Melanie
        The bot object.
    obj : `discord.Message` or `discord.Member` or `discord.Role`
        The object to check permissions for.

    Returns
    -------
    bool
        :code:`True` if the object has mod permissions.

    Raises
    ------
    TypeError
        If the wrong type of ``obj`` was passed.

    """
    if isinstance(obj, discord.Message):
        user = obj.author
    elif isinstance(obj, discord.Member):
        user = obj
    elif isinstance(obj, discord.Role):
        gid = obj.guild.id
        if obj in await bot.get_admin_role_ids(gid):
            return True
        if obj in await bot.get_mod_role_ids(gid):
            return True
        return False
    else:
        msg = "Only messages, members or roles may be passed"
        raise TypeError(msg)

    if await bot.is_owner(user):
        return True
    if await bot.is_mod(user):
        return True

    return False


def strfdelta(delta: timedelta) -> str:
    """Format a timedelta object to a message with time units.

    Parameters
    ----------
    delta : datetime.timedelta
        The duration to parse.

    Returns
    -------
    str
        A message representing the timedelta with units.

    """
    s = []
    if delta.days:
        ds = "%i day" % delta.days
        if delta.days > 1:
            ds += "s"
        s.append(ds)
    hrs, rem = divmod(delta.seconds, 60 * 60)
    if hrs:
        hs = "%i hr" % hrs
        if hrs > 1:
            hs += "s"
        s.append(hs)
    mins, secs = divmod(rem, 60)
    if mins:
        s.append("%i min" % mins)
    if secs:
        s.append("%i sec" % secs)
    return " ".join(s)


async def is_admin_or_superior(bot: Melanie, obj: Union[discord.Message, discord.Member, discord.Role]) -> bool:
    """Same as `is_mod_or_superior` except for admin permissions.

    If a message is passed, its author's permissions are checked. If a role is
    passed, it simply checks if it is the admin role.

    Parameters
    ----------
    bot : melaniebot.core.bot.Melanie
        The bot object.
    obj : `discord.Message` or `discord.Member` or `discord.Role`
        The object to check permissions for.

    Returns
    -------
    bool
        :code:`True` if the object has admin permissions.

    Raises
    ------
    TypeError
        If the wrong type of ``obj`` was passed.

    """
    if isinstance(obj, discord.Message):
        user = obj.author
    elif isinstance(obj, discord.Member):
        user = obj
    elif isinstance(obj, discord.Role):
        return obj.id in await bot.get_admin_role_ids(obj.guild.id)
    else:
        msg = "Only messages, members or roles may be passed"
        raise TypeError(msg)

    if await bot.is_owner(user):
        return True
    if await bot.is_admin(user):
        return True

    return False


async def check_permissions(ctx: Context, perms: dict[str, bool]) -> bool:
    """Check if the author has required permissions.

    This will always return ``True`` if the author is a bot owner, or
    has the ``administrator`` permission. If ``perms`` is empty, this
    will only check if the user is a bot owner.

    Parameters
    ----------
    ctx : Context
        The command invocation context to check.
    perms : Dict[str, bool]
        A dictionary mapping permissions to their required states.
        Valid permission names are those listed as properties of
        the `discord.Permissions` class.

    Returns
    -------
    bool
        ``True`` if the author has the required permissions.

    """
    if await ctx.bot.is_owner(ctx.author):
        return True
    elif not perms:
        return False
    resolved = ctx.channel.permissions_for(ctx.author)

    return resolved.administrator or all(getattr(resolved, name, None) == value for name, value in perms.items())
