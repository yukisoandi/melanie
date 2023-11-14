from __future__ import annotations

import asyncio
import contextlib
import datetime
import typing

import discord
import regex as re
from melaniebot.core import Config, checks, commands
from melaniebot.core.utils.chat_formatting import humanize_list

from melanie import get_parent_var, make_e

if typing.TYPE_CHECKING:
    from melaniebot.core.bot import Melanie

UNIQUE_ID = 0x6AFE8000


class Gallery(commands.Cog):
    """Set channels as galleries, deleting all messages that don't contain any
    attachments.
    """

    __version__ = "1.3.1"

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=564154651321346431, force_registration=True)
        self.settings_cache = {}
        self.config.register_guild(channels=[], whitelist=None, time=0)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        context = super().format_help_for_context(ctx)
        return f"{context}\n\nVersion: {self.__version__}"

    async def update_cache(self) -> None:
        ctx = get_parent_var("ctx") or get_parent_var("_ctx")
        if ctx.guild.id in self.settings_cache:
            del self.settings_cache[ctx.guild.id]

    @commands.group(autohelp=True)
    @commands.guild_only()
    @checks.has_permissions(manage_messages=True)
    async def galleryset(self, ctx: commands.Context) -> None:
        """Various Gallery settings."""

    @galleryset.command(name="add")
    async def galleryset_add(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Add a channel to the list of Gallery channels."""
        if channel.id not in await self.config.guild(ctx.guild).channels():
            async with self.config.guild(ctx.guild).channels() as channels:
                channels.append(channel.id)
            await ctx.send(embed=make_e(f"{channel.mention} has been set as a gallery channel."))
        else:
            await ctx.send(embed=make_e(f"{channel.mention} is already a gallery channel.", status=2))

        await self.update_cache()

    @galleryset.command(name="remove")
    async def galleryset_remove(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Remove a channel from the list of Gallery channels."""
        if channel.id in await self.config.guild(ctx.guild).channels():
            async with self.config.guild(ctx.guild).channels() as channels:
                channels.remove(channel.id)
                embed = make_e(f"{channel.mention} has been removed as a gallery channel.")
            await ctx.send(embed=embed)
        else:
            embed = make_e(f"{channel.mention} is not a gallery channel.", status=2)
            await ctx.send(embed=embed)
        await self.update_cache()

    @galleryset.command(name="role")
    async def galleryset_role(self, ctx: commands.Context, role: typing.Optional[discord.Role]) -> None:
        """Add or remove a whitelisted role."""
        if not role:
            await self.config.guild(ctx.guild).whitelist.clear()
            embed = make_e("Whitelisted roles cleared.")
        else:
            await self.config.guild(ctx.guild).whitelist.set(role.id)
            embed = make_e(f"{role.mention} has been whitelisted.")
        await ctx.send(embed=embed)
        await self.update_cache()

    @galleryset.command(name="time")
    async def galleryset_time(self, ctx: commands.Context, time: int) -> None:
        """Set how long (in seconds!!) the bot should wait before deleting non
        images.

        0 to reset (default time)

        """
        await self.config.guild(ctx.guild).time.set(time)
        embed = make_e(f"I will wait {time} seconds before deleting messages that are not images.")
        await ctx.send(embed=embed)
        await self.update_cache()

    @galleryset.command(name="settings")
    async def galleryset_settings(self, ctx: commands.Context) -> None:
        """See current settings."""
        data = await self.config.guild(ctx.guild).all()

        channels = []
        for c_id in data["channels"]:
            if c := ctx.guild.get_channel(c_id):
                channels.append(c.mention)
        channels = humanize_list(channels) if channels else "None"

        role = ctx.guild.get_role(data["whitelist"])
        role = role.name if role else "None"

        embed = discord.Embed(colour=await ctx.embed_colour(), timestamp=datetime.datetime.now())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        embed.title = "**__Gallery Settings:__**"
        embed.set_footer(text="*required to function properly")

        embed.add_field(name="Gallery channels*:", value=channels)
        embed.add_field(name="Whitelisted role:", value=role)
        embed.add_field(name="Wait time:", value=str(data["time"]))

        await ctx.send(embed=embed)
        await self.update_cache()

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        if not message.guild:
            return
        if not self.bot.is_ready():
            return
        guild: discord.Guild = message.guild
        if guild.id not in self.settings_cache:
            self.settings_cache[guild.id] = await self.config.guild(message.guild).all()
        settings = self.settings_cache[guild.id]
        if message.channel.id not in settings["channels"]:
            return
        if not message.attachments:
            uris = re.findall(r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+", message.content)
            if len(uris) == 1:
                uri = "".join(uris)
                uri = uri.split("?")[0]
                parts = uri.split(".")
                extension = parts[-1]
                imageTypes = ["jpg", "jpeg", "tiff", "png", "gif", "bmp"]
                if extension in imageTypes:
                    return
            time = settings["time"]
            if rid := settings["whitelist"]:
                role = message.guild.get_role(int(rid))
                if role and role in message.author.roles:
                    return
            if time != 0:
                await asyncio.sleep(time)
            with contextlib.suppress(discord.NotFound):
                await message.delete()
