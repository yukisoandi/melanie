import contextlib
from typing import Tuple, Union

import discord
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from linkquoter.converters import LinkToMessage
from melanie import cancel_tasks, footer_gif, get_redis, log
from melanie.core import spawn_task
from melanie.helpers import get_image_colors2


class LinkQuoter(commands.Cog):
    """Quote Discord message links."""

    __version__ = "1.1.1"

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6234567898747434823, force_registration=True)
        default_guild = {"on": True, "webhooks": True, "cross_server": True, "respect_perms": False, "delete": False}
        self.config.register_guild(**default_guild)
        self.enabled_guilds = set()
        self.active_tasks = []
        spawn_task(self.initialize(), self.active_tasks)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def initialize(self):
        await self.bot.waits_uptime_for(30)
        for guild_id, guild_data in (await self.config.all_guilds()).items():
            if guild_data["on"]:
                self.enabled_guilds.add(guild_id)

    @staticmethod
    def get_name(user: Union[discord.Member, discord.User]) -> str:
        return user.display_name if hasattr(user, "display_name") else user.name

    async def get_messages(self, guild: discord.Guild, author: discord.Member, links: list):
        messages = []
        for link in links:
            link_segments = link.split("/")
            link_ids = []
            for segment in link_segments[-3:]:
                try:
                    link_ids.append(int(segment))
                except ValueError:
                    continue
            if link_ids[0] != guild.id:
                continue
            channel = guild.get_channel(link_ids[1])
            if (
                not channel
                or channel.is_nsfw()
                or not channel.permissions_for(author).read_messages
                or not channel.permissions_for(author).read_message_history
            ):
                continue
            if not (channel.permissions_for(guild.me).read_messages and channel.permissions_for(guild.me).read_message_history):
                continue
            try:
                message = await channel.fetch_message(link_ids[2])
                messages.append(message)
            except discord.errors.NotFound:
                continue
        return messages

    async def message_to_embed(
        self,
        message: discord.Message,
        *,
        invoke_guild: discord.Guild = None,
        author_field: bool = True,
        footer_field: bool = True,
    ) -> discord.Embed | None:
        image = None
        e: discord.Embed = None
        if message.embeds:
            embed = message.embeds[0]
            if str(embed.type) == "rich":
                if footer_field:
                    embed.timestamp = message.created_at
                e = embed
            if str(embed.type) in {"image", "article"}:
                image = embed.url
        if not e:
            content = message.content
            e = discord.Embed(description=content, timestamp=message.created_at)
        if author_field:
            lookup = await get_image_colors2(str(message.author.avatar_url))
            if lookup:
                e.color = lookup.dominant.decimal

            e.set_author(name=f"{message.author.display_name} said..", icon_url=message.author.avatar_url, url=message.jump_url)

        if footer_field:
            if invoke_guild and message.guild != invoke_guild:
                e.set_footer(icon_url=message.guild.icon_url, text=f"#{message.channel.name} @ {message.guild}")
            else:
                e.set_footer(text=f"#{message.channel.name}", icon_url=footer_gif)

        if message.attachments:
            att = message.attachments[0]
            image = att.proxy_url
            e.add_field(name="Attachments", value=f"[{att.filename}]({att.url})", inline=False)

        if not image and (stickers := getattr(message, "stickers", [])):
            for sticker in stickers:
                if sticker.image_url:
                    image = str(sticker.image_url)
                    e.add_field(name="Stickers", value=f"[{sticker.name}]({image})", inline=False)
                    break

        if image:
            e.set_image(url=image)

        if ref := message.reference:
            ref_message = ref.cached_message or (ref.resolved if ref.resolved and isinstance(ref.resolved, discord.Message) else None)
            if not ref_message and (ref_chan := message.guild.get_channel(ref.channel_id)):
                with contextlib.suppress(discord.Forbidden, discord.NotFound):
                    ref_message = await ref_chan.fetch_message(ref.message_id)
            if ref_message:
                jump_url = ref_message.jump_url
                e.add_field(
                    name="Replying to",
                    value=f"[{ref_message.content[:1000] if ref_message.content else 'Click to view attachments'}]({jump_url})",
                    inline=False,
                )
        e.add_field(name="Source", value=f'\n[jump]({message.jump_url} "Follow me to the original message!")', inline=False)
        return e

    async def create_embeds(
        self,
        messages: list,
        *,
        invoke_guild: discord.Guild = None,
        author_field: bool = True,
        footer_field: bool = True,
    ) -> list[Tuple[discord.Embed, discord.Member]]:
        embeds = []
        for message in messages:
            embed = await self.message_to_embed(message, invoke_guild=invoke_guild, author_field=author_field, footer_field=footer_field)
            if embed:
                embeds.append((embed, message.author))
        return embeds

    @commands.guild_only()
    @commands.command(aliases=["linkmessage"])
    async def linkquote(self, ctx, message_link: LinkToMessage = None):
        """Quote a message from a link."""
        if not message_link:
            if not hasattr(ctx.message, "reference") or not (ref := ctx.message.reference):
                raise commands.BadArgument
            message_link = ref.resolved or await ctx.guild.get_channel(ref.channel_id).fetch_message(ref.message_id)
        embed = await self.message_to_embed(message_link, invoke_guild=ctx.guild, author_field=True)
        await ctx.send(embed=embed)

    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.group()
    async def linkquoteset(self, ctx: commands.Context):
        """Manage LinkQuoter settings."""

    @linkquoteset.command(name="auto")
    async def linkquoteset_auto(self, ctx, true_or_false: bool = None):
        """Toggle automatic link-quoting.

        Enabling this will make [botname] attempt to quote any message link that is sent in this server.
        [botname] will ignore any message that has "no quote" in it.
        If the user doesn't have permission to view the channel that they link, it will not quote.

        To enable quoting from other servers, run `[p]linkquoteset global`.

        To prevent spam, links can be automatically quoted 3 times every 10 seconds.
        """
        target_state = true_or_false if true_or_false is not None else not (await self.config.guild(ctx.guild).on())
        await self.config.guild(ctx.guild).on.set(target_state)
        if target_state:
            await ctx.send("I will now automatically quote links.")
            self.enabled_guilds.add(ctx.guild.id)
        else:
            await ctx.send("I will no longer automatically quote links.")
            self.enabled_guilds.remove(ctx.guild.id)

    @linkquoteset.command(name="delete")
    async def linkquoteset_delete(self, ctx, true_or_false: bool = None):
        """Toggle deleting of messages for automatic quoting.

        If automatic quoting is enabled, then [botname] will also delete messages that contain links in them.
        """
        target_state = true_or_false if true_or_false is not None else not (await self.config.guild(ctx.guild).delete())
        await self.config.guild(ctx.guild).delete.set(target_state)
        if target_state:
            await ctx.send("I will now delete messages when automatically quoting.")
        else:
            await ctx.send("I will no longer delete messages when automatically quoting.")

    @linkquoteset.command(name="global")
    async def linkquoteset_global(self, ctx, true_or_false: bool = None):
        """Toggle cross-server quoting.

        Turning this setting on will allow this server to quote other servers, and other servers to quote this one.
        """
        target_state = true_or_false if true_or_false is not None else not (await self.config.guild(ctx.guild).cross_server())
        await self.config.guild(ctx.guild).cross_server.set(target_state)
        if target_state:
            await ctx.send(
                "This server is now opted in to cross-server quoting. This server can now quote other servers, and other servers can quote this one.",
            )
        else:
            await ctx.send("This server is no longer opted in to cross-server quoting.")

    @checks.bot_has_permissions(manage_webhooks=True)
    @linkquoteset.command(name="webhook")
    async def linkquoteset_webhook(self, ctx, true_or_false: bool = None):
        """Toggle whether [botname] should use webhooks to quote.

        [botname] must have Manage Webhook permissions to use webhooks when quoting.
        """
        target_state = true_or_false if true_or_false is not None else not (await self.config.guild(ctx.guild).webhooks())
        await self.config.guild(ctx.guild).webhooks.set(target_state)
        if target_state:
            await ctx.send("I will now use webhooks to quote.")
        else:
            await ctx.send("I will no longer use webhooks to quote.")

    @linkquoteset.command(name="settings")
    async def linkquoteset_settings(self, ctx: commands.Context):
        """View LinkQuoter settings."""
        data = await self.config.guild(ctx.guild).all()
        description = [
            f"**Automatic Quoting:** {data['on']}",
            f"**Cross-Server:** {data['cross_server']}",
            f"**Delete Messages:** {data['delete']}",
            f"**Use Webhooks:** {data['webhooks']}",
        ]
        e = discord.Embed(color=await ctx.embed_color(), description="\n".join(description))
        e.set_author(name=f"{ctx.guild} LinkQuoter Settings", icon_url=ctx.guild.icon_url)
        await ctx.send(embed=e)

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        if message.author.bot or isinstance(message.author, discord.User):
            return
        if not message.guild:
            return
        sent = False
        if "no quote" in message.content.lower():
            return
        guild: discord.Guild = message.guild
        channel: discord.TextChannel = message.channel
        ctx = commands.Context(
            message=message,
            author=message.author,
            guild=guild,
            channel=channel,
            me=message.guild.me,
            bot=self.bot,
            prefix="auto_linkquote",
            command=self.bot.get_command("  "),
        )
        if not await self.bot.message_eligible_as_command(message):
            return
        try:
            quoted_message = await LinkToMessage().convert(ctx, message.content)
        except commands.BadArgument:
            return
        data = await self.config.guild(ctx.guild).all()
        redis = get_redis()
        if await redis.ratelimited(f"imgquote2:{message.channel.id}", 10, 30):
            return log.error("Ratlimited for {} @ {}", message.author, message.channel.id)
        embed = await self.message_to_embed(quoted_message, invoke_guild=ctx.guild, author_field=True)
        await message.channel.send(embed=embed)
        sent = True
        if sent and data["delete"]:
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.delete()
