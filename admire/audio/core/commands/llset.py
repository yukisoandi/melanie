from __future__ import annotations

from pathlib import Path

import discord
from melaniebot.core import commands
from melaniebot.core.utils.chat_formatting import box

from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass


def _(x):
    return x


class LavalinkSetupCommands(MixinMeta, metaclass=CompositeMetaClass):
    @commands.group(name="llsetup", aliases=["llset"])
    @commands.is_owner()
    async def command_llsetup(self, ctx: commands.Context) -> None:
        """Lavalink server configuration options."""

    @command_llsetup.command(name="java")
    async def command_llsetup_java(self, ctx: commands.Context, *, java_path: str = None):
        """Change your Java executable path.

        Enter nothing to reset to default.

        """
        external = await self.config.use_external_lavalink()
        if external:
            return await self.send_embed_msg(
                ctx,
                title="Invalid Environment",
                description="You cannot changed the Java executable path of external Lavalink instances from the Audio Cog.",
            )
        if java_path is None:
            await self.config.java_exc_path.clear()
            await self.send_embed_msg(ctx, title="Java Executable Reset", description="Audio will now use `java` to run your Lavalink.jar")
        else:
            exc = Path(java_path)
            exc_absolute = exc.absolute()
            if not exc.exists() or not exc.is_file():
                return await self.send_embed_msg(
                    ctx,
                    title="Invalid Environment",
                    description=("`{java_path}` is not a valid executable").format(java_path=exc_absolute),
                )
            await self.config.java_exc_path.set(str(exc_absolute))
            await self.send_embed_msg(
                ctx,
                title="Java Executable Changed",
                description=("Audio will now use `{exc}` to run your Lavalink.jar").format(exc=exc_absolute),
            )
        try:
            if self.player_manager is not None:
                await self.player_manager.shutdown()
        except ProcessLookupError:
            await self.send_embed_msg(
                ctx,
                title="Failed To Shutdown Lavalink",
                description=("For it to take effect please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
            )
        else:
            try:
                self.lavalink_restart_connect()
            except ProcessLookupError:
                await self.send_embed_msg(
                    ctx,
                    title="Failed To Shutdown Lavalink",
                    description=("Please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
                )

    @command_llsetup.command(name="external")
    async def command_llsetup_external(self, ctx: commands.Context) -> None:
        """Toggle using external Lavalink servers."""
        external = await self.config.use_external_lavalink()
        await self.config.use_external_lavalink.set(not external)

        if external:
            embed = discord.Embed(
                title="Setting Changed",
                description=("External Lavalink server: {true_or_false}.").format(true_or_false="Disabled" if external else ("Enabled")),
            )

            await self.send_embed_msg(ctx, embed=embed)
        else:
            try:
                if self.player_manager is not None:
                    await self.player_manager.shutdown()
            except ProcessLookupError:
                await self.send_embed_msg(
                    ctx,
                    title="Failed To Shutdown Lavalink",
                    description=("External Lavalink server: {true_or_false}\nFor it to take effect please reload Audio (`{prefix}reload audio`).").format(
                        true_or_false="Disabled" if external else ("Enabled"),
                        prefix=ctx.prefix,
                    ),
                )

            else:
                await self.send_embed_msg(
                    ctx,
                    title="Setting Changed",
                    description=("External Lavalink server: {true_or_false}.").format(true_or_false="Disabled" if external else ("Enabled")),
                )

        try:
            self.lavalink_restart_connect()
        except ProcessLookupError:
            await self.send_embed_msg(
                ctx,
                title="Failed To Shutdown Lavalink",
                description=("Please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
            )

    @command_llsetup.command(name="host")
    async def command_llsetup_host(self, ctx: commands.Context, host: str) -> None:
        """Set the Lavalink server host."""
        await self.config.host.set(host)
        footer = None
        if await self.update_external_status():
            footer = "External Lavalink server set to True."
        await self.send_embed_msg(ctx, title="Setting Changed", description=("Host set to {host}.").format(host=host), footer=footer)
        try:
            self.lavalink_restart_connect()
        except ProcessLookupError:
            await self.send_embed_msg(
                ctx,
                title="Failed To Shutdown Lavalink",
                description=("Please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
            )

    @command_llsetup.command(name="password")
    async def command_llsetup_password(self, ctx: commands.Context, password: str) -> None:
        """Set the Lavalink server password."""
        await self.config.password.set(password)
        footer = None
        if await self.update_external_status():
            footer = "External Lavalink server set to True."
        await self.send_embed_msg(ctx, title="Setting Changed", description=("Server password set to {password}.").format(password=password), footer=footer)

        try:
            self.lavalink_restart_connect()
        except ProcessLookupError:
            await self.send_embed_msg(
                ctx,
                title="Failed To Shutdown Lavalink",
                description=("Please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
            )

    @command_llsetup.command(name="wsport")
    async def command_llsetup_wsport(self, ctx: commands.Context, ws_port: int) -> None:
        """Set the Lavalink websocket server port."""
        await self.config.ws_port.set(ws_port)
        footer = None
        if await self.update_external_status():
            footer = "External Lavalink server set to True."
        await self.send_embed_msg(ctx, title="Setting Changed", description=("Websocket port set to {port}.").format(port=ws_port), footer=footer)

        try:
            self.lavalink_restart_connect()
        except ProcessLookupError:
            await self.send_embed_msg(
                ctx,
                title="Failed To Shutdown Lavalink",
                description=("Please reload Audio (`{prefix}reload audio`).").format(prefix=ctx.prefix),
            )

    @command_llsetup.command(name="info", aliases=["settings"])
    async def command_llsetup_info(self, ctx: commands.Context) -> None:
        """Display Lavalink connection settings."""
        configs = await self.config.all()
        host = configs["host"]
        password = configs["password"]
        rest_port = configs["rest_port"]
        ws_port = configs["ws_port"]
        msg = "----" + "Connection Settings" + "----        \n"
        msg += ("Host:             [{host}]\n").format(host=host)
        msg += ("WS Port:          [{port}]\n").format(port=ws_port)
        if ws_port != rest_port != 2333:
            msg += ("Rest Port:        [{port}]\n").format(port=rest_port)
        msg += ("Password:         [{password}]\n").format(password=password)
        try:
            await self.send_embed_msg(ctx.author, description=box(msg, lang="ini"))
        except discord.HTTPException:
            await ctx.send("I need to be able to DM you to send you this info.")
