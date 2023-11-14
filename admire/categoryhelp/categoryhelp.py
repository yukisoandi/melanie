from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Hashable, Optional, Sequence

from async_lru import alru_cache
from melaniebot.cogs.alias.alias import Alias
from melaniebot.cogs.alias.alias_entry import AliasEntry
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from rapidfuzz import utils
from rapidfuzz.fuzz import ratio

from melanie import checkpoint, log
from runtimeopt import offloaded

if TYPE_CHECKING:
    from rapidfuzz.process_py import extractOne
else:
    from rapidfuzz.process_cpp import extractOne


@offloaded
def extract(query, choices) -> tuple[Sequence[Hashable], int | float, int] | None:
    return extractOne(query, choices, scorer=ratio, processor=utils.default_process, score_cutoff=80)


class CategoryHelp(commands.Cog):
    """Command for getting help for category that ignores case-sensitivity."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.help2 = None

    @alru_cache(ttl=35)
    async def load_aliases(self) -> dict[str, AliasEntry]:
        alias: Alias = self.bot.get_cog("Alias")

        final = {}

        async with alias.config.all() as all:
            entries = all.get("entries")
            for e in entries:
                await checkpoint()
                e2 = AliasEntry.from_json(e)

                final[e2.name] = e2

        return final

    @alru_cache(ttl=15)
    async def get_options(self):
        idx = {}

        for cog_name, cog in self.bot.cogs.items():
            cog_name: str
            await checkpoint()
            if cog_name.lower().startswith("modlog"):
                continue
            idx[cog_name] = cog

        for cmd in self.bot.walk_commands():
            cmd: commands.Command
            await checkpoint()
            idx[cmd.qualified_name] = cmd

        return idx

    @alru_cache(ttl=30)
    async def find_cog(self, value):
        value = value.lower()
        idx = await self.get_options()
        result = await extract(value, set(idx.keys()))
        log.info(result)
        if result:
            key = result[0]
            return idx[key]

    @commands.command(name="help", aliases=["h"], hidden=True)
    async def categoryhelp(self, ctx: commands.Context, *, category_name: Optional[str]) -> None:
        """Get help for category."""
        if not category_name:
            return await self.bot.send_help_for(ctx, None, from_help_command=True)

        async with asyncio.timeout(10):
            if cog := self.bot.get_command(category_name):
                return await self.bot.send_help_for(ctx, cog, from_help_command=True)

            if cog := self.bot.get_cog(category_name):
                return await self.bot.send_help_for(ctx, cog, from_help_command=True)

            aliases = await self.load_aliases()
            if (alias := aliases.get(category_name)) and (cmd := self.bot.get_command(alias.command)):
                return await self.bot.send_help_for(ctx, cmd, from_help_command=True)

            cog = await self.find_cog(category_name)
            if cog:
                await self.bot.send_help_for(ctx, cog, from_help_command=True)
