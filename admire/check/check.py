from __future__ import annotations

import asyncio
import contextlib

import discord
from melaniebot.core import checks, commands


def _(x):
    return x


class Check(commands.Cog):
    """Check."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.command(hidden=True)
    @checks.has_permissions(administrator=True)
    async def check(self, ctx: commands.Context, member: discord.Member):
        """Complete information lookup of a user. Shows the users current device
        if online, their modlog history, shared servers, message count per
        server, role membership, generates a wordcloud of words used, and
        more..

        ***Note:** This command's output will very depending on whether or not you have Melanie Paid+ access.

        """
        ctx.assume_yes = True

        lookup_msg = await ctx.send(f"🔎 Starting lookup for: {member.mention}({member.id})")

        with contextlib.suppress(asyncio.exceptions.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(
                    self._wca(ctx, member),
                    self._onlineinfo(ctx, member),
                    self._deeplookup(ctx, member),
                    self._userinfo(ctx, member),
                    self._whois_user(ctx, member),
                    self._warnings_or_read(ctx, member),
                    ctx.tick(),
                ),
                timeout=320,
            )
        with contextlib.suppress(discord.NotFound):
            await lookup_msg.delete()
        return await ctx.send("Lookup complete.")

    """"async def allservers(
        self,
        ctx: commands.Context,
        user: discord.User = None,
        hours: int = None,
        colormap: Optional[str] = "Pastel1",
        maxwords: Optional[int] = 400,
        smallest: Optional[str] = 2,"""

    async def _userinfo(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("userinfo"), user=member)

    async def _wca(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("wordcloud allservers"), hours=None, user=member, colormap="Pastel1", maxwords=400, smallest=2)

    async def _deeplookup(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("deeplookup"), user=member)

    async def _onlineinfo(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("onlineinfo"), member=member)

    async def _warnings_or_read(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("warnings"), user=member)

    # async def _maybe_altcheck(self, ctx:commands.Context, member):
    #   except:
    #     pass

    async def _whois_user(self, ctx: commands.Context, member) -> None:
        with contextlib.suppress(Exception):
            await ctx.invoke(ctx.bot.get_command("whois"), user_id=member.id)
