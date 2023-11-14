"""commands.converter
==================
This module contains useful functions and classes for command argument conversion.

Some of the converters within are included provisionally and are marked as such.
"""
from __future__ import annotations

import functools
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, Optional, TypeVar
from typing import Optional as NoParseOptional
from typing import Union as UserInputOptional

import discord
import regex as re
from dateutil.relativedelta import relativedelta
from discord.ext import commands as dpy_commands
from discord.ext.commands import BadArgument

from melaniebot.core.utils.chat_formatting import humanize_list, humanize_timedelta

if TYPE_CHECKING:
    from .context import Context

__all__ = [
    "DictConverter",
    "UserInputOptional",
    "NoParseOptional",
    "RelativedeltaConverter",
    "TimedeltaConverter",
    "get_dict_converter",
    "get_timedelta_converter",
    "parse_relativedelta",
    "parse_timedelta",
    "Literal",
    "CommandConverter",
    "CogConverter",
]


def _(x):
    return x


ID_REGEX = re.compile(r"([0-9]{15,20})")

# Taken with permission from
# https://github.com/mikeshardmind/SinbadCogs/blob/816f3bc2ba860243f75112904b82009a8a9e1f99/scheduler/time_utils.py#L9-L19
TIME_RE_STRING = "((?P<years>\\d+?)\\s?(years?|y))?\\s?((?P<months>\\d+?)\\s?(months?|mo))?\\s?((?P<weeks>\\d+?)\\s?(weeks?|w))?\\s?((?P<days>\\d+?)\\s?(days?|d))?\\s?((?P<hours>\\d+?)\\s?(hours?|hrs|hr?))?\\s?((?P<minutes>\\d+?)\\s?(minutes?|mins?|m(?!o)))?\\s?((?P<seconds>\\d+?)\\s?(seconds?|secs?|s))?"  # prevent matching "months"

TIME_RE = re.compile(TIME_RE_STRING, re.I)


def _parse_and_match(string_to_match: str, allowed_units: list[str]) -> Optional[dict[str, int]]:
    """Local utility function to match TIME_RE string above to user input for both
    parse_timedelta and parse_relativedelta.
    """
    if matches := TIME_RE.match(string_to_match):
        params = {k: int(v) for k, v in matches.groupdict().items() if v is not None}
        for k in params:
            if k not in allowed_units:
                msg = f"`{k}` is not a valid unit of time for this command"
                raise BadArgument(msg)
        return params
    return None


def parse_timedelta(
    argument: str,
    *,
    maximum: Optional[timedelta] = None,
    minimum: Optional[timedelta] = None,
    allowed_units: Optional[list[str]] = None,
) -> Optional[timedelta]:
    """This converts a user provided string into a timedelta.

    The units should be in order from largest to smallest.
    This works with or without whitespace.

    Parameters
    ----------
    argument : str
        The user provided input
    maximum : Optional[datetime.timedelta]
        If provided, any parsed value higher than this will raise an exception
    minimum : Optional[datetime.timedelta]
        If provided, any parsed value lower than this will raise an exception
    allowed_units : Optional[List[str]]
        If provided, you can constrain a user to expressing the amount of time
        in specific units. The units you can chose to provide are the same as the
        parser understands. (``weeks``, ``days``, ``hours``, ``minutes``, ``seconds``)

    Returns
    -------
    Optional[datetime.timedelta]
        If matched, the timedelta which was parsed. This can return `None`

    Raises
    ------
    BadArgument
        If the argument passed uses a unit not allowed, but understood
        or if the value is out of bounds.

    """
    allowed_units = allowed_units or ["weeks", "days", "hours", "minutes", "seconds"]
    if params := _parse_and_match(argument, allowed_units):
        try:
            delta = timedelta(**params)
        except OverflowError as e:
            msg = "The time set is way too high, consider setting something reasonable."
            raise BadArgument(msg) from e

        if maximum and maximum < delta:
            msg = f"This amount of time is too large for this command. (Maximum: {humanize_timedelta(timedelta=maximum)})"
            raise BadArgument(msg)
        if minimum and delta < minimum:
            msg = f"This amount of time is too small for this command. (Minimum: {humanize_timedelta(timedelta=minimum)})"
            raise BadArgument(msg)
        return delta
    return None


def parse_relativedelta(argument: str, *, allowed_units: Optional[list[str]] = None) -> Optional[relativedelta]:
    """This converts a user provided string into a datetime with offset from NOW.

    The units should be in order from largest to smallest.
    This works with or without whitespace.

    Parameters
    ----------
    argument : str
        The user provided input
    allowed_units : Optional[List[str]]
        If provided, you can constrain a user to expressing the amount of time
        in specific units. The units you can chose to provide are the same as the
        parser understands. (``years``, ``months``, ``weeks``, ``days``, ``hours``, ``minutes``, ``seconds``)

    Returns
    -------
    Optional[dateutil.relativedelta.relativedelta]
        If matched, the relativedelta which was parsed. This can return `None`

    Raises
    ------
    BadArgument
        If the argument passed uses a unit not allowed, but understood
        or if the value is out of bounds.

    """
    allowed_units = allowed_units or ["years", "months", "weeks", "days", "hours", "minutes", "seconds"]
    if params := _parse_and_match(argument, allowed_units):
        try:
            delta = relativedelta(**params)
        except OverflowError as e:
            msg = "The time set is way too high, consider setting something reasonable."
            raise BadArgument(msg) from e

        return delta
    return None


class _GuildConverter(discord.Guild):
    """Converts to a `discord.Guild` object.

    The lookup strategy is as follows (in order):

    1. Lookup by ID.
    2. Lookup by name.

    .. deprecated-removed:: 3.4.8 60
        ``GuildConverter`` is now only provided within ``melaniebot.core.commands`` namespace.

    """

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> discord.Guild:
        return await dpy_commands.GuildConverter().convert(ctx, argument)


_GuildConverter.__name__ = "GuildConverter"


def __getattr__(name: str, *, stacklevel: int = 2) -> Any:
    # Let me just say it one more time... This is awesome! (PEP-562)
    if name == "GuildConverter":
        # let's not waste time on importing this when we don't need it
        # (and let's not put in the public API)
        from melaniebot.core.utils._internal_utils import deprecated_removed

        deprecated_removed(
            "`GuildConverter` from `melaniebot.core.commands.converter` namespace",
            "3.4.8",
            60,
            "Use `GuildConverter` from `melaniebot.core.commands` namespace instead.",
            stacklevel=2,
        )
        return globals()["_GuildConverter"]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    return [*globals().keys(), "GuildConverter"]


# Below this line are a lot of lies for mypy about things that *end up* correct when
# These are used for command conversion purposes. Please refer to the portion
# which is *not* for type checking for the actual implementation
# and ensure the lies stay correct for how the object should look as a typehint

if TYPE_CHECKING:
    DictConverter = dict[str, str]
else:

    class DictConverter(dpy_commands.Converter):
        """Converts pairs of space separated values to a dict."""

        def __init__(self, *expected_keys: str, delims: Optional[list[str]] = None) -> None:
            self.expected_keys = expected_keys
            self.delims = delims or [" "]
            self.pattern = re.compile(r"|".join(re.escape(d) for d in self.delims))

        async def convert(self, ctx: Context, argument: str) -> dict[str, str]:
            ret: dict[str, str] = {}
            args = self.pattern.split(argument)

            if len(args) % 2 != 0:
                raise BadArgument

            iterator = iter(args)

            for key in iterator:
                if self.expected_keys and key not in self.expected_keys:
                    msg = f"Unexpected key {key}"
                    raise BadArgument(msg)

                ret[key] = next(iterator)

            return ret


if TYPE_CHECKING:

    def get_dict_converter(*expected_keys: str, delims: Optional[list[str]] = None) -> type[dict]:
        ...

else:

    def get_dict_converter(*expected_keys: str, delims: Optional[list[str]] = None) -> type[dict]:
        """Returns a typechecking safe `DictConverter` suitable for use with
        discord.py.
        """

        class PartialMeta(type):
            __call__ = functools.partialmethod(type(DictConverter).__call__, *expected_keys, delims=delims)

        class ValidatedConverter(DictConverter, metaclass=PartialMeta):
            pass

        return ValidatedConverter


if TYPE_CHECKING:
    TimedeltaConverter = timedelta
else:

    class TimedeltaConverter(dpy_commands.Converter):
        """This is a converter for timedeltas. The units should be in order from
        largest to smallest. This works with or without whitespace.

        See `parse_timedelta` for more information about how this functions.

        Attributes
        ----------
        maximum : Optional[datetime.timedelta]
            If provided, any parsed value higher than this will raise an exception
        minimum : Optional[datetime.timedelta]
            If provided, any parsed value lower than this will raise an exception
        allowed_units : Optional[List[str]]
            If provided, you can constrain a user to expressing the amount of time
            in specific units. The units you can choose to provide are the same as the
            parser understands: (``weeks``, ``days``, ``hours``, ``minutes``, ``seconds``)
        default_unit : Optional[str]
            If provided, it will additionally try to match integer-only input into
            a timedelta, using the unit specified. Same units as in ``allowed_units``
            apply.

        """

        def __init__(self, *, minimum=None, maximum=None, allowed_units=None, default_unit=None) -> None:
            self.allowed_units = allowed_units
            self.default_unit = default_unit
            self.minimum = minimum
            self.maximum = maximum

        async def convert(self, ctx: Context, argument: str) -> timedelta:
            if self.default_unit and argument.isdecimal():
                argument += self.default_unit

            delta = parse_timedelta(argument, minimum=self.minimum, maximum=self.maximum, allowed_units=self.allowed_units)

            if delta is not None:
                return delta
            raise BadArgument  # This allows this to be a required argument.


if TYPE_CHECKING:

    def get_timedelta_converter(
        *,
        default_unit: Optional[str] = None,
        maximum: Optional[timedelta] = None,
        minimum: Optional[timedelta] = None,
        allowed_units: Optional[list[str]] = None,
    ) -> type[timedelta]:
        ...

else:

    def get_timedelta_converter(
        *,
        default_unit: Optional[str] = None,
        maximum: Optional[timedelta] = None,
        minimum: Optional[timedelta] = None,
        allowed_units: Optional[list[str]] = None,
    ) -> type[timedelta]:
        """This creates a type suitable for typechecking which works with
        discord.py's commands.

        See `parse_timedelta` for more information about how this functions.

        Parameters
        ----------
        maximum : Optional[datetime.timedelta]
            If provided, any parsed value higher than this will raise an exception
        minimum : Optional[datetime.timedelta]
            If provided, any parsed value lower than this will raise an exception
        allowed_units : Optional[List[str]]
            If provided, you can constrain a user to expressing the amount of time
            in specific units. The units you can choose to provide are the same as the
            parser understands: (``weeks``, ``days``, ``hours``, ``minutes``, ``seconds``)
        default_unit : Optional[str]
            If provided, it will additionally try to match integer-only input into
            a timedelta, using the unit specified. Same units as in ``allowed_units``
            apply.

        Returns
        -------
        type
            The converter class, which will be a subclass of `TimedeltaConverter`

        """

        class PartialMeta(type):
            __call__ = functools.partialmethod(
                type(DictConverter).__call__,
                allowed_units=allowed_units,
                default_unit=default_unit,
                minimum=minimum,
                maximum=maximum,
            )

        class ValidatedConverter(TimedeltaConverter, metaclass=PartialMeta):
            pass

        return ValidatedConverter


if TYPE_CHECKING:
    RelativedeltaConverter = relativedelta
else:

    class RelativedeltaConverter(dpy_commands.Converter):
        """This is a converter for relative deltas.

        The units should be in order from largest to smallest.
        This works with or without whitespace.

        See `parse_relativedelta` for more information about how this functions.

        Attributes
        ----------
        allowed_units : Optional[List[str]]
            If provided, you can constrain a user to expressing the amount of time
            in specific units. The units you can choose to provide are the same as the
            parser understands: (``years``, ``months``, ``weeks``, ``days``, ``hours``, ``minutes``, ``seconds``)
        default_unit : Optional[str]
            If provided, it will additionally try to match integer-only input into
            a timedelta, using the unit specified. Same units as in ``allowed_units``
            apply.

        """

        def __init__(self, *, allowed_units=None, default_unit=None) -> None:
            self.allowed_units = allowed_units
            self.default_unit = default_unit

        async def convert(self, ctx: Context, argument: str) -> relativedelta:
            if self.default_unit and argument.isdecimal():
                argument += self.default_unit

            delta = parse_relativedelta(argument, allowed_units=self.allowed_units)

            if delta is not None:
                return delta
            raise BadArgument  # This allows this to be a required argument.


if not TYPE_CHECKING:

    class NoParseOptional:
        """This can be used instead of `typing.Optional` to avoid discord.py
        special casing the conversion behavior.

        .. seealso::     The `ignore_optional_for_conversion` option of
        commands.

        """

        def __class_getitem__(cls, key):
            if isinstance(key, tuple):
                msg = "Must only provide a single type to Optional"
                raise TypeError(msg)
            return key


_T = TypeVar("_T")

if not TYPE_CHECKING:
    #: This can be used when user input should be converted as discord.py
    #: treats `typing.Optional`, but the type should not be equivalent to
    #: ``typing.Union[DesiredType, None]`` for type checking.
    #:
    #: Note: In type checking context, this type hint can be passed
    #: multiple types, but such usage is not supported and will fail at runtime
    #:
    #: .. warning::
    #:    This converter class is still provisional.
    UserInputOptional = Optional

if not TYPE_CHECKING:

    class Literal(dpy_commands.Converter):
        """This can be used as a converter for `typing.Literal`.

        In a type checking context it is `typing.Literal`.
        In a runtime context, it's a converter which only matches the literals it was given.


        .. warning::
            This converter class is still provisional.

        """

        def __init__(self, valid_names: tuple[str]) -> None:
            self.valid_names = valid_names

        def __call__(self, ctx, arg):
            # Callable's are treated as valid types:
            # https://github.com/python/cpython/blob/3.8/Lib/typing.py#L148
            # Without this, ``typing.Union[Literal["clear"], bool]`` would fail
            return self.convert(ctx, arg)

        async def convert(self, ctx, arg):
            if arg in self.valid_names:
                return arg
            msg = f"Expected one of: {humanize_list(self.valid_names)}"
            raise BadArgument(msg)

        def __class_getitem__(cls, k):
            if not k:
                msg = "Need at least one value for Literal"
                raise ValueError(msg)
            return cls(k) if isinstance(k, tuple) else cls((k,))


if TYPE_CHECKING:
    CommandConverter = dpy_commands.Command
    CogConverter = dpy_commands.Cog
else:

    class CommandConverter(dpy_commands.Converter):
        """Converts a command name to the matching
        `melaniebot.core.commands.Command` object.
        """

        async def convert(self, ctx: Context, argument: str):
            arg = argument.strip()
            if command := ctx.bot.get_command(arg):
                return command
            else:
                raise BadArgument(_('Command "{arg}" not found.').format(arg=arg))

    class CogConverter(dpy_commands.Converter):
        """Converts a cog name to the matching `melaniebot.core.commands.Cog`
        object.
        """

        async def convert(self, ctx: Context, argument: str):
            arg = argument.strip()
            if cog := ctx.bot.get_cog(arg):
                return cog
            else:
                raise BadArgument(_('Cog "{arg}" not found.').format(arg=arg))
