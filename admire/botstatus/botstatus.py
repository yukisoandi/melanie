from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord.ext import tasks
from melaniebot.core import Config, checks, commands


def _(x):
    return x


class Botstatus(commands.Cog):
    """Botstatus."""

    __version__ = "1.0.0"

    def __init__(self, bot) -> None:
        self.ready = False
        self.bot = bot
        self.config = Config.get_conf(self, identifier=30052000, force_registration=True)
        standard = {"status": (None, None, None)}
        self.config.register_global(**standard)
        self.ready = True
        self.start_task: Optional[asyncio.Task] = None
        self._update_task.start()

    def init(self) -> None:
        self.start_task = asyncio.create_task(self.fromconf())

    def cog_unload(self) -> None:
        self._update_task.cancel()
        if self.start_task:
            self.start_task.cancel()

    @tasks.loop(seconds=30)
    async def _update_task(self) -> None:
        await self.fromconf()

    async def setfunc(self, sType, status, text) -> None:
        # This will get removed in future versions and is to ensure config backwards-compatibility
        if sType == "game":
            sType = "playing"

        t = getattr(discord.ActivityType, sType, False)
        s = getattr(discord.Status, status, False)
        if not (t and s):
            return
        activity = discord.Activity(name=text, type=t)
        await self.bot.change_presence(status=s, activity=activity)

    async def fromconf(self) -> None:
        await self.bot.wait_until_ready()
        value = await self.config.status()
        if value[0] and value[1] and value[2]:
            await self.setfunc(value[0], value[1], value[2])

    @commands.group()
    @checks.is_owner()
    async def botstatus(self, ctx) -> None:
        """Set a status that doesn't dissappear on reboot.

        Usage: ;botstatus <type> <status> <text>

        """

    @botstatus.group(name="playing", aliases=["game"])
    async def game(self, ctx) -> None:
        """Usage: ;botstatus playing <status> <text>."""

    @game.command(name="online")
    async def g_online(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("playing", "online", text))
            await self.setfunc("playing", "online", text)
            await ctx.send(f"Status set to ``Online | Playing {text}``")

    @game.command(name="idle")
    async def g_idle(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("playing", "idle", text))
            await self.setfunc("playing", "idle", text)
            await ctx.send(f"Status set to ``Idle | Playing {text}``")

    @game.command(name="dnd")
    async def g_dnd(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("playing", "dnd", text))
            await self.setfunc("playing", "dnd", text)
            await ctx.send(f"Status set to ``DND | Playing {text}``")

    @game.command(name="offline")
    async def g_offline(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("playing", "offline", text))
            await self.setfunc("playing", "offline", text)
            await ctx.send(f"Status set to ``Offline | Playing {text}``")

    @botstatus.group()
    async def listening(self, ctx) -> None:
        """Usage: ;botstatus listening <status> <text>."""

    @listening.command(name="online")
    async def l_online(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("listening", "online", text))
            await self.setfunc("listening", "online", text)
            await ctx.send(f"Status set to ``Online | Listening to {text}``")

    @listening.command(name="idle")
    async def l_idle(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("listening", "idle", text))
            await self.setfunc("listening", "idle", text)
            await ctx.send(f"Status set to ``Idle | Listening to {text}``")

    @listening.command(name="dnd")
    async def l_dnd(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("listening", "dnd", text))
            await self.setfunc("listening", "dnd", text)
            await ctx.send(f"Status set to ``DND | Listening to {text}``")

    @listening.command(name="offline")
    async def l_offline(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("listening", "offline", text))
            await self.setfunc("listening", "offline", text)
            await ctx.send(f"Status set to ``Offline | Listening to {text}``")

    @botstatus.group()
    async def watching(self, ctx) -> None:
        """Usage: ;botstatus watching <status> <text>."""

    @watching.command(name="online")
    async def w_online(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("watching", "online", text))
            await self.setfunc("watching", "online", text)
            await ctx.send(f"Status set to ``Online | Watching {text}``")

    @watching.command(name="idle")
    async def w_idle(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("watching", "idle", text))
            await self.setfunc("watching", "idle", text)
            await ctx.send(f"Status set to ``Idle | Watching {text}``")

    @watching.command(name="dnd")
    async def w_dnd(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("watching", "dnd", text))
            await self.setfunc("watching", "dnd", text)
            await ctx.send(f"Status set to ``DND | Watching {text}``")

    @watching.command(name="offline")
    async def w_offline(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("watching", "offline", text))
            await self.setfunc("watching", "offline", text)
            await ctx.send(f"Status set to ``Offline | Watching {text}``")

    @botstatus.group()
    async def competing(self, ctx) -> None:
        """Set a competing status."""

    @competing.command(name="online")
    async def c_online(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("competing", "online", text))
            await self.setfunc("competing", "online", text)
            await ctx.send(f"Status set to ``Online | Competing {text}``")

    @competing.command(name="away")
    async def c_away(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("competing", "away", text))
            await self.setfunc("competing", "away", text)
            await ctx.send(f"Status set to ``Away | Competing {text}``")

    @competing.command(name="dnd")
    async def c_dnd(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("competing", "dnd", text))
            await self.setfunc("competing", "dnd", text)
            await ctx.send(f"Status set to ``DND | Competing {text}``")

    @competing.command(name="offline")
    async def c_offline(self, ctx: commands.Context, *, text: str) -> None:
        if len(text) > 128:
            await ctx.send("The chracter limit for status messages is 128.")
        else:
            await self.config.status.set(("competing", "offline", text))
            await self.setfunc("competing", "offline", text)
            await ctx.send(f"Status set to ``Offline | Competing {text}``")

    @botstatus.command()
    async def clear(self, ctx) -> None:
        """Clear the saved botstatus and disable auto-setting on reboot."""
        await self.config.status.set((None, None, None))
        await ctx.send("Saved botstatus has been cleared.")
        await self.bot.change_presence(status=discord.Status.online, activity=None)
