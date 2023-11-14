from __future__ import annotations

from typing import Union

import discord
from loguru import logger as log
from melaniebot.core import commands

from .abc import MixinMeta
from .converters import FuzzyRole


class AutoRole(MixinMeta):
    """Manage autoroles and sticky roles."""

    async def initialize(self) -> None:
        log.debug("AutoRole Initialize")
        await super().initialize()

    @commands.is_owner()
    @commands.admin_or_permissions(manage_roles=True)
    @commands.group(name="autorole")
    async def _autorole(self, ctx: commands.Context) -> None:
        """Manage autoroles and sticky roles."""

    @_autorole.command()
    async def add(self, ctx: commands.Context, *, role: FuzzyRole) -> None:
        """Add a role to be added to all new members on join."""

    @_autorole.command()
    async def remove(self, ctx: commands.Context, *, role: Union[FuzzyRole, int]) -> None:
        """Remove an autorole."""

    @_autorole.group(name="humans")
    async def _humans(self, ctx: commands.Context) -> None:
        """Manage autoroles for humans."""

    @_humans.command(name="add")
    async def humans_add(self, ctx: commands.Context, *, role: FuzzyRole) -> None:
        """Add a role to be added to all new humans on join."""

    @_humans.command(name="remove")
    async def humans_remove(self, ctx: commands.Context, *, role: Union[FuzzyRole, int]) -> None:
        """Remove an autorole for humans."""

    @_autorole.group(name="bots")
    async def _bots(self, ctx: commands.Context) -> None:
        """Manage autoroles for bots."""

    @_bots.command(name="add")
    async def bots_add(self, ctx: commands.Context, *, role: FuzzyRole) -> None:
        """Add a role to be added to all new bots on join."""

    @_bots.command(name="remove")
    async def bots_remove(self, ctx: commands.Context, *, role: Union[FuzzyRole, int]) -> None:
        """Remove an autorole for bots."""

    @_autorole.group(invoke_without_command=True, name="sticky")
    async def _sticky(self, ctx: commands.Context, true_or_false: bool = None) -> None:
        """Toggle whether the bot should reapply roles on member joins and leaves."""

    @_sticky.command(aliases=["bl"])
    async def blacklist(self, ctx: commands.Context, *, role: FuzzyRole) -> None:
        """Blacklist a role from being reapplied on joins."""

    @_sticky.command(aliases=["unbl"])
    async def unblacklist(self, ctx: commands.Context, *, role: Union[FuzzyRole, int]) -> None:
        """Remove a role from the sticky blacklist."""

    # @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        pass

    # @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        pass
