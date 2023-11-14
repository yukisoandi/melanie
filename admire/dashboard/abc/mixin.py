from __future__ import annotations

from melaniebot.core import commands


@commands.group(name="dashboard", hidden=True)
async def dashboard(self, ctx: commands.Context) -> None:
    """Group command for controlling the web dashboard for Melanie."""


class DBMixin:
    """This is mostly here to easily mess with things..."""

    c = dashboard
