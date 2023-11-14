from __future__ import annotations

from collections.abc import Awaitable, Coroutine, Generator
from typing import TYPE_CHECKING, Any, Callable, Protocol, TypeVar, Union, overload

import discord
from discord.ext import commands as dpy_commands

from .context import Context

# So much of this can be stripped right back out with proper stubs.
if not TYPE_CHECKING:
    from discord.ext.commands import (
        after_invoke,
        before_invoke,
        bot_has_any_role,
        bot_has_role,
        check,
        cooldown,
        dm_only,
        guild_only,
        has_any_role,
        has_role,
        is_nsfw,
    )


def _(x):
    return x


"""
Anything here is either a reimplementation or re-export of a discord.py
function or class with more lies for mypy.
"""

__all__ = [
    "check",
    # "check_any",  # discord.py 1.3
    "guild_only",
    "dm_only",
    "is_nsfw",
    "has_role",
    "has_any_role",
    "bot_has_role",
    "bot_has_any_role",
    "when_mentioned_or",
    "cooldown",
    "when_mentioned",
    "before_invoke",
    "after_invoke",
]

_CT = TypeVar("_CT", bound=Context)
_T = TypeVar("_T")
_F = TypeVar("_F")
CheckType = Union[Callable[[_CT], bool], Callable[[_CT], Coroutine[Any, Any, bool]]]
CoroLike = Callable[..., Union[Awaitable[_T], Generator[Any, None, _T]]]
InvokeHook = Callable[[_CT], Coroutine[Any, Any, bool]]


class CheckDecorator(Protocol):
    predicate: Coroutine[Any, Any, bool]

    @overload
    def __call__(self, func: _CT) -> _CT:
        ...

    @overload
    def __call__(self, func: CoroLike) -> CoroLike:
        ...


if TYPE_CHECKING:

    def check(predicate: CheckType) -> CheckDecorator:
        ...

    def guild_only() -> CheckDecorator:
        ...

    def dm_only() -> CheckDecorator:
        ...

    def is_nsfw() -> CheckDecorator:
        ...

    def has_role() -> CheckDecorator:
        ...

    def has_any_role() -> CheckDecorator:
        ...

    def bot_has_role() -> CheckDecorator:
        ...

    def bot_has_any_role() -> CheckDecorator:
        ...

    def cooldown(rate: int, per: float, type: dpy_commands.BucketType = ...) -> Callable[[_F], _F]:
        ...

    def before_invoke(coro: InvokeHook) -> Callable[[_F], _F]:
        ...

    def after_invoke(coro: InvokeHook) -> Callable[[_F], _F]:
        ...


PrefixCallable = Callable[[dpy_commands.bot.BotBase, discord.Message], list[str]]


def when_mentioned(bot: dpy_commands.bot.BotBase, msg: discord.Message) -> list[str]:
    return [f"<@{bot.user.id}> ", f"<@!{bot.user.id}> "]


def when_mentioned_or(*prefixes) -> PrefixCallable:
    def inner(bot: dpy_commands.bot.BotBase, msg: discord.Message) -> list[str]:
        r = list(prefixes)
        r = when_mentioned(bot, msg) + r
        return r

    return inner
