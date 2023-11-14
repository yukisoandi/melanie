# Say by retke, aka El Laggron


from __future__ import annotations

from typing import Optional

import discord
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.utils.tunnel import Tunnel

from melanie import create_task, log


def _(x):
    return x


class Say(commands.Cog):
    """Speak as if you were the bot.

    Documentation: http://laggron.melanie/say.html

    """

    def __init__(self, bot) -> None:
        self.bot = bot
        self.interaction = []

    __version__ = "1.6.0"

    async def say(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel],
        text: str,
        files: list,
        mentions: discord.AllowedMentions = None,
        delete: int = None,
    ) -> None:
        if not channel:
            channel = ctx.channel
        if not text and not files:
            await ctx.send_help()
            return

        # preparing context info in case of an error
        error_message = (
            f"Has files: yes\nNumber of files: {len(files)}\nFiles URL: " + ", ".join([x.url for x in ctx.message.attachments]) if files else "Has files: no"
        )

        # sending the message
        try:
            await channel.send(text, files=files, allowed_mentions=mentions, delete_after=delete)
        except discord.errors.HTTPException:
            author = ctx.author
            if not ctx.guild.me.permissions_in(channel).send_messages:
                try:
                    await ctx.send(f"I am not allowed to send messages in {channel.mention}", delete_after=2)
                except discord.errors.Forbidden:
                    await author.send(f"I am not allowed to send messages in {channel.mention}", delete_after=15)
                    # If this fails then fuck the command author
            elif not ctx.guild.me.permissions_in(channel).attach_files:
                try:
                    await ctx.send(f"I am not allowed to upload files in {channel.mention}", delete_after=2)
                except discord.errors.Forbidden:
                    await author.send(f"I am not allowed to upload files in {channel.mention}", delete_after=15)
            else:
                log.error(f"Unknown permissions error when sending a message.\n{error_message}")

    @checks.is_owner()
    @commands.command(name="say")
    @checks.admin_or_permissions(administrator=True)
    async def _say(self, ctx: commands.Context, channel: Optional[discord.TextChannel], *, text: str = "") -> None:
        """Make the bot say what you want in the desired channel.

        If no channel is specified, the message will be send in the current channel.
        You can attach some files to upload them to Discord.

        Example usage :
        - `!say #general hello there`
        - `!say owo I have a file` (a file is attached to the command message)

        """
        files = await Tunnel.files_from_attatch(ctx.message)
        await self.say(ctx, channel, text, files)

    @checks.is_owner()
    @commands.command(name="sayad")
    @checks.admin_or_permissions(administrator=True)
    async def _sayautodelete(self, ctx: commands.Context, channel: Optional[discord.TextChannel], delete_delay: int, *, text: str = "") -> None:
        """Same as say command, except it deletes the said message after a set
        number of seconds.
        """
        files = await Tunnel.files_from_attatch(ctx.message)
        await self.say(ctx, channel, text, files, delete=delete_delay)

    @checks.is_owner()
    @commands.command(name="sayd", aliases=["sd"])
    @checks.admin_or_permissions(administrator=True)
    async def _saydelete(self, ctx: commands.Context, channel: Optional[discord.TextChannel], *, text: str = "") -> None:
        """Same as say command, except it deletes your message.

        If the message wasn't removed, then I don't have enough
        permissions.

        """
        # download the files BEFORE deleting the message
        author = ctx.author
        files = await Tunnel.files_from_attatch(ctx.message)

        try:
            await ctx.message.delete()
        except discord.errors.Forbidden:
            try:
                await ctx.send("Not enough permissions to delete messages.", delete_after=2)
            except discord.errors.Forbidden:
                await author.send("Not enough permissions to delete messages.", delete_after=15)

        await self.say(ctx, channel, text, files)

    @checks.is_owner()
    @commands.command(name="saym", aliases=["sm"])
    @checks.admin_or_permissions(administrator=True)
    async def _saymention(self, ctx: commands.Context, channel: Optional[discord.TextChannel], *, text: str = ""):
        """Same as say command, except role and mass mentions are enabled."""
        message = ctx.message
        channel = channel or ctx.channel
        guild = channel.guild
        files = await Tunnel.files_from_attach(message)
        role_mentions = message.role_mentions

        no_mention = [x for x in role_mentions if x.mentionable is False]
        if guild.me.guild_permissions.administrator is False:
            if no_mention:
                await ctx.send(
                    f"I can't mention the following roles: {', '.join([x.name for x in no_mention])}\nTurn on mentions or make me an admin on the server.\n",
                )
                return
            if message.mention_everyone and channel.permissions_for(guild.me).mention_everyone is False:
                await ctx.send("I don't have the permission to mention everyone.")
                return
        if message.mention_everyone and channel.permissions_for(ctx.author).mention_everyone is False:
            await ctx.send("You don't have the permission yourself to do mass mentions.")
            return
        if ctx.author.guild_permissions.administrator is False and no_mention:
            await ctx.send(
                f"You're not allowed to mention the following roles: {', '.join([x.name for x in no_mention])}\nTurn on mentions for that role or be an admin in the server.\n",
            )
            return

        text = text.replace("e1", "@everyone")
        log.info(text)
        await self.say(ctx, channel, text, files, mentions=discord.AllowedMentions.all())

    # @commands.command(name="interact")
    # @checks.admin_or_permissions(administrator=True)
    # async def _interact(self, ctx: commands.Context, channel: discord.TextChannel = None):
    #     """Start receiving and sending messages as the bot through DM"""

    #     if channel is None:
    #         if isinstance(ctx.channel, discord.DMChannel):
    #             await ctx.send(

    #     if u in self.interaction:

    #         _(
    #             "Just send me any message and I will send it in that channel.\n"
    #             "React with ❌ on this message to end the session.\n"
    #             "If no message was send or received in the last 5 minutes, "
    #             "the request will time out and stop."
    #         ).format(channel.mention)

    #     while True:

    #         if u not in self.interaction:

    #         if message.author == u and isinstance(message.channel, discord.DMChannel):
    #             if message.content.startswith(tuple(await self.bot.get_valid_prefixes())):
    #             embed.set_author(

    #             if message.attachments != []:

    async def stop_interaction(self, user) -> None:
        self.interaction.remove(user)
        await user.send("Session closed")

    def cog_unload(self) -> None:
        log.debug("Unloading cog...")
        for user in self.interaction:
            create_task(self.stop_interaction(user))
