"""jishaku.functools ~~~~~~~~~~~~~~~~~.

Function-related tools for Jishaku.

:copyright: (c) 2021 Devon (Gorialis) R
:license: MIT, see LICENSE for more details.

"""


from __future__ import annotations

import asyncio
import contextlib
import functools
import typing

# pylint: disable=invalid-name
T = typing.TypeVar("T")


P = typing.ParamSpec("P")  # pylint: disable=no-member


def executor_function(sync_function: typing.Callable[P, T]) -> typing.Callable[P, typing.Awaitable[T]]:
    @functools.wraps(sync_function)
    async def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
        loop = asyncio.get_event_loop()
        internal_function = functools.partial(sync_function, *args, **kwargs)
        return await loop.run_in_executor(None, internal_function)

    return sync_wrapper


def threaded(sync_function: typing.Callable[P, T]) -> typing.Callable[P, typing.Awaitable[T]]:
    """A decorator that wraps a sync function in an executor, changing it into an
    async function.
    """

    @functools.wraps(sync_function)
    async def sync_wrapper(*args, **kwargs):
        """Asynchronous function that wraps a sync function with an executor."""
        return await asyncio.get_event_loop().run_in_executor(None, functools.partial(sync_function, *args, **kwargs))

    return sync_wrapper


class AsyncSender:
    """Storage and control flow class that allows prettier value sending to async
    iterators.

    Example:
    -------
    .. code:: python3

        async def foo():
            print("foo yielding 1")
            x = yield 1
            print(f"foo received {x}")
            yield 3

        async for send, result in AsyncSender(foo()):
            print(f"asyncsender received {result}")
            send(2)

    Produces:

    .. code::

        foo yielding 1
        asyncsender received 1
        foo received 2
        asyncsender received 3

    """

    __slots__ = ("iterator", "send_value")

    def __init__(self, iterator) -> None:
        self.iterator = iterator
        self.send_value = None

    def __aiter__(self):
        return self._internal(self.iterator.__aiter__())

    async def _internal(self, base):
        with contextlib.suppress(StopAsyncIteration):
            while True:
                # Send the last value to the iterator
                value = await base.asend(self.send_value)
                # Reset it incase one is not sent next iteration
                self.send_value = None
                # Yield sender and iterator value
                yield self.set_send_value, value

    def set_send_value(self, value) -> None:
        """Sets the next value to be sent to the iterator.

        This is provided by iteration of this class and should not be
        called directly.

        """
        self.send_value = value
