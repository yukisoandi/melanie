from __future__ import annotations

from datetime import timedelta

from melaniebot.core import checks, commands
from melaniebot.core.utils.chat_formatting import humanize_timedelta

from .abc import MixinMeta  # type: ignore


def _(x):
    return x


class Slowmode(MixinMeta):
    """Commands regarding channel slowmode management."""

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def slowmode(
        self,
        ctx,
        *,
        interval: commands.TimedeltaConverter(minimum=timedelta(seconds=0), maximum=timedelta(hours=6), default_unit="seconds") = timedelta(seconds=0),
    ):
        """Changes channel's slowmode setting.

        Interval can be anything from 0 seconds to 6 hours. Use without
        parameters to disable.

        """
        seconds = interval.total_seconds()
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds > 0:
            await ctx.send(f"Slowmode interval is now {humanize_timedelta(timedelta=interval)}.")
        else:
            await ctx.send("Slowmode has been disabled.")
