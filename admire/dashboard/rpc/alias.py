from __future__ import annotations

import discord
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import commands
from melaniebot.core.utils.chat_formatting import humanize_list

from dashboard.helpers import escape

from .utils import permcheck, rpccheck


class DashboardRPC_AliasCC:
    def __init__(self, cog: commands.Cog) -> None:
        self.bot: Melanie = cog.bot
        self.cog: commands.Cog = cog

        # Initialize RPC handlers
        self.bot.register_rpc_handler(self.fetch_aliases)

    def unload(self) -> None:
        self.bot.unregister_rpc_handler(self.fetch_aliases)

    @rpccheck()
    @permcheck("Alias", ["aliascc"])
    async def fetch_aliases(self, guild: discord.Guild, member: discord.Member):
        aliascog = self.bot.get_cog("Alias")
        aliases = await aliascog._aliases.get_guild_aliases(guild)

        ida = {}
        for alias in aliases:
            command = f"{alias.command[:47]}..." if len(alias.command) > 50 else alias.command
            if alias.command not in ida:
                ida[alias.command] = {"aliases": [], "shortened": escape(command)}
            ida[alias.command]["aliases"].append(f"{escape(alias.name)}")

        return {
            command: {
                "humanized": humanize_list([f"<code>{x}</code>" for x in aliases["aliases"]]),
                "raw": aliases["aliases"],
                "shortened": aliases["shortened"],
            }
            for command, aliases in ida.items()
        }
