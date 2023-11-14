from __future__ import annotations

import asyncio
import contextlib
import datetime
from collections import defaultdict
from functools import partial
from typing import Union

import arrow
import discord
import regex as re
import ujson as json
from humanize import intcomma
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import box
from regex.regex import Pattern

__all__ = ["UNIQUE_ID", "Welc"]

UNIQUE_ID = 78552482
_DEFAULT_WELCOME = "Welcome, {mention}, to {server}!\n\n{count} user{plural} joined today!"
START_CODE_BLOCK_RE: Pattern[str] = re.compile(r"^((```json)(?=\s)|(```))")


class APIError(Exception):
    pass


class Welc(commands.Cog):
    """A welcomer that doesnt spam. Checks to make sure that a user has a role
    first. Supports sending embeds.

    by: monty

    """

    def __init__(self, bot: Melanie) -> None:
        self.conf: Config = Config.get_conf(self, identifier=UNIQUE_ID, force_registration=True)
        self.config = self.conf
        self.bot: Melanie = bot
        self.conf.register_channel(enabled=False, last_message=None, delete_last_message=False, welcome_msg=_DEFAULT_WELCOME, welcome_text=None)
        self.conf.register_guild(count=0, day=None, join_role=None)
        self.guilds_enabled = []
        self.joinrole = {}
        self.welcome_locks = defaultdict(partial(asyncio.BoundedSemaphore, 2))
        self.channel_settings_cache = {}

    async def init(self) -> None:
        self.settings_cache = await self.conf.all_guilds()
        self.channel_settings_cache = await self.conf.all_channels()

    @staticmethod
    def cleanup_code(content) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return START_CODE_BLOCK_RE.sub("", content)[:-3]
        return content

    @checks.has_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.group(hidden=True, invoke_without_command=True, aliases=["wcount"])
    async def welc(self, ctx: commands.Context) -> None:
        """Manage settings for welc."""
        if not ctx.invoked_subcommand:
            await ctx.send_help()
            channel: discord.TextChannel = ctx.channel
            settings = self.conf.channel(channel)
            if await settings.enabled():
                msg: str = await settings.welcome_msg()
                delete_last: bool = await settings.delete_last_message()
                await ctx.send(box(f"Enabled in this channel.\nDeletion of previous welcome message enabled: {delete_last}\nWelcome message: {msg}"))
            else:
                await ctx.send(box("Disabled in this channel."))

    async def alt_request(self, member: str) -> str:
        return "Timeout"

    @welc.command(name="toggle")
    async def welc_toggle(self, ctx: commands.Context) -> None:
        """Toggle welcome messages in this channel."""
        channel: discord.TextChannel = ctx.channel
        settings = self.conf.channel(channel)
        now_enabled: bool = not await settings.enabled()
        await settings.enabled.set(now_enabled)
        await ctx.send(f"Welcome messages are now {'enabled' if now_enabled else 'disabled'} in this channel.")

    @welc.command(name="message")
    async def welc_message(self, ctx: commands.Context, *, message: str) -> None:
        """Set the bot's welcome message.

        This message can be formatted using these parameters:
            mention - Mention the user who joined
            avatar_url - URL of the avatar of the user who joined
            created_at - Date user was created
            trustlevel - Altdentifier's trust level
            username - The user's display name
            server - The name of the server
            count - The number of users who joined today.
            plural - Empty if `count` is 1. 's' otherwise.
            total - The total number of users in the server.
        To format the welcome message with the above parameters, include them
        in your message surrounded by curly braces {}.

        """
        channel: discord.TextChannel = ctx.channel
        settings = self.conf.channel(channel)

        member: discord.Member = ctx.author
        count: int = await self.conf.guild(ctx.guild).count()
        params = {
            "<mention>": member.mention,
            "<username>": str(member),
            "<server>": ctx.guild.name,
            "<count>": count,
            "<plural>": "" if count == 1 else "s",
            "<total>": intcomma(ctx.guild.member_count),
        }
        params |= await self.build_custom_params(member)
        welcome_text = await settings.welcome_text()
        try:
            json.loads(message)
            await self.send_to_channel_json(ctx=ctx, data=message, channel=channel, welcome_text=welcome_text, params=params)
            await settings.welcome_msg.set(message)
            await ctx.tick()
        except TypeError or ValueError:
            await ctx.send("That's invalid JSON, I couldn't parse it. ")
        except KeyError as exc:
            try:
                to_send = message.format(**params)
                await ctx.send(to_send)
            except Exception:
                await ctx.send(
                    f"The welcome message cannot be formatted, because it contains an invalid placeholder `{exc.args[0]}`. See `{ctx.clean_prefix}help welc message` for a list of valid placeholders.",
                )

        await self.init()

    async def build_custom_params(self, member: discord.Member) -> dict:
        trust_level = await self.alt_request(member.id)
        return {"<created_at>": member.created_at.strftime("%m/%d/%Y"), "<trust_level>": trust_level, "<avatar_url>": member.avatar_url}

    @welc.command(name="deletelast")
    async def welc_deletelast(self, ctx: commands.Context) -> None:
        """Toggle deleting the previous welcome message in this channel.

        When enabled, the last message is deleted *only* if it was sent
        on the same day as the new welcome message.

        """
        channel: discord.TextChannel = ctx.channel
        settings = self.conf.channel(channel)
        now_deleting: bool = not await settings.delete_last_message()
        await settings.delete_last_message.set(now_deleting)
        await ctx.send(f"Deleting welcome messages are now {'enabled' if now_deleting else 'disabled'} in this channel.")

    @welc.command(name="text", ignore_extra=False)
    async def welc_text(self, ctx: commands.Context, *, message=None) -> None:
        """Set the text (including role pings) that you want to have on user
        welcome.
        """
        if not message:
            message = None
        settings = self.conf.channel(ctx.channel)
        await settings.welcome_text.set(message)
        await ctx.tick()
        await self.init()

    @welc.command(name="joinrole")
    async def welc_joinrole(self, ctx: commands.Context, *, role: Union[discord.Role, str]) -> None:
        """Set a role which a user must receive before they're welcomed.

        This means that, instead of the welcome message being sent when
        the user joins the server, the welcome message will be sent when
        they receive a particular role.

        Use `;welc joinrole disable` to revert to the default behaviour.

        """
        if isinstance(role, discord.Role):
            await self.conf.guild(ctx.guild).join_role.set(role.id)
            await ctx.tick()
            self.joinrole = {}
        elif role.lower() == "disable":
            await self.conf.guild(ctx.guild).join_role.clear()
            await ctx.tick()
        else:
            await ctx.send(f'Role "{role}" not found.')
        await self.init()

    async def send_to_channel_json(self, ctx, data: str, params: dict, channel: discord.TextChannel, welcome_text: str, setup_user: bool = False) -> None:
        if not isinstance(data, str):
            return
        data = data.replace("\n", " ").replace("\r", "")
        if isinstance(welcome_text, str):
            for old, new in params.items():
                welcome_text = welcome_text.replace(old, str(new))
        for old, new in params.items():
            data = data.replace(old, str(new))
        try:
            data = json.loads(data)
            to_send = discord.Embed().from_dict(data)
            if "thumbnail" in data:
                to_send.set_thumbnail(url=data.get("thumbnail"))
            if "image" in data:
                to_send.set_image(url=data.get("image"))

            sent_message = await channel.send(
                embed=to_send,
                content=welcome_text,
                allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=True, replied_user=True),
            )
            channel_settings = self.conf.channel(channel)
            await channel_settings.last_message.set(sent_message.id)

        except ValueError or TypeError:
            if setup_user:
                await ctx.send(data)
                ctx.send(data)

    async def send_welcome_message(self, member: discord.Member, ctx=None) -> None:
        guild: discord.Guild = member.guild
        if self.welcome_locks[guild.id].locked():
            return log.warning(f"Welcomes are locked @ {member.guild}")
        async with self.welcome_locks[guild.id]:
            async with asyncio.timeout(5):
                async with self.config.guild(guild).all() as guild_settings:
                    today: datetime.date = datetime.date.today()
                    new_day: bool = False
                    if guild_settings["day"] == str(today):
                        cur_count: int = guild_settings["count"]
                        guild_settings.update(count=cur_count + 1)
                    else:
                        new_day = True
                        guild_settings["day"] = str(today)
                        guild_settings["count"] = 1
                    welcome_channels: list[discord.TextChannel] = []
                    channel: discord.TextChannel

                    for channel in guild.channels:
                        if channel.id not in self.channel_settings_cache:
                            continue
                        if self.channel_settings_cache[channel.id]["enabled"]:
                            welcome_channels.append(channel)
                    for channel in welcome_channels:
                        channel_settings = await self.conf.channel(channel).all()
                        delete_last: bool = channel_settings["delete_last_message"]
                        if delete_last and not new_day:
                            last_message: int = channel_settings["last_message"]
                            try:
                                last_message: discord.Message = await channel.fetch_message(last_message)
                            except discord.HTTPException:
                                # Perhaps the message was deleted
                                pass
                            else:
                                with contextlib.suppress(discord.NotFound):
                                    await last_message.delete()
                        count: int = guild_settings["count"]
                        params = {
                            "<mention>": member.mention,
                            "<username>": str(member),
                            "<server>": guild.name,
                            "<count>": count,
                            "<plural>": "" if count == 1 else "s",
                            "<total>": intcomma(guild.member_count),
                        }
                        welcome: str = channel_settings["welcome_msg"]
                        welcome_text = channel_settings["welcome_text"]
                        params |= await self.build_custom_params(member)
                        try:
                            try:
                                json.loads(welcome)
                            except ValueError:
                                await self.conf.channel(channel).clear()
                                await self.conf.guild(channel.guild).clear()
                                log.error("Bad Json for {} {} ({}) - Clearing configuration for channel and server", channel, channel.guild, channel.guild.id)

                            await self.send_to_channel_json(ctx=ctx, data=welcome, channel=channel, params=params, welcome_text=welcome_text)
                        except KeyError as exc:
                            try:
                                to_send = welcome.format(**params)
                                await ctx.send(to_send)
                            except Exception:
                                await ctx.send(
                                    f"The welcome message cannot be formatted, because it contains an invalid placeholder `{exc.args[0]}`. See `{ctx.clean_prefix}help welc message` for a list of valid placeholders.",
                                )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Send the welcome message and update the last message."""
        try:
            guild: discord.Guild = member.guild
            if guild.id not in self.settings_cache:
                return

            join_role = guild.get_role(self.settings_cache[member.guild.id]["join_role"])

            if not join_role:
                await self.send_welcome_message(member)
        except Exception:
            log.exception("welc join ")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.bot.is_ready():
            return
        try:
            guild: discord.Guild = after.guild

            if guild.id not in self.settings_cache:
                return
            join_role = guild.get_role(self.settings_cache[guild.id]["join_role"])
            if not join_role:
                return

            if join_role in before.roles and join_role in after.roles:
                return
            if arrow.now().shift(minutes=-30).naive > after.joined_at:
                return

            before_roles = frozenset(before.roles)
            after_roles = frozenset(after.roles)
            try:
                added_role = next(iter(after_roles - before_roles))
            except StopIteration:
                # A role wasn't added
                return
            if added_role.id == join_role.id:
                await self.send_welcome_message(after)
        except:
            log.exception("welc member update")
            raise
