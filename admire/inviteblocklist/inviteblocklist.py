from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from re import Pattern
from typing import Any, Optional, Union

import discord
import orjson
import regex as re
from discord.ext.commands.converter import IDConverter, InviteConverter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list, pagify

from melanie import BaseModel, alru_cache, checkpoint, default_lock_cache, spawn_task
from melanie.helpers import make_e


def _(x):
    return x


INVITE_RE: Pattern = re.compile(r"(?:https?\:\/\/)?discord(?:\.gg|(?:app)?\.com\/invite)\/(.+)", re.I)


class GuildSettings(BaseModel):
    whitelist: list[int] = []
    enabled: bool = False
    immunity_list: list[int] = []
    ignore_mods: bool = True


class InvalidInvitePayload:
    status = 404
    reason = "invalid"

    class Response:
        status = 404

    response = Response()
    message = "Invalid invite"


class SerialGuild(BaseModel):
    name: Optional[str]
    id: Optional[int]


class SerialInvite(BaseModel):
    max_age: Optional[Any]
    code: Optional[str]
    guild: Optional[SerialGuild]


class ValidServerID(IDConverter):
    async def convert(self, ctx: commands.Context, argument: str):
        if match := self._get_id_match(argument):
            return int(match.group(1))
        msg = "The ID provided does not appear to be valid."
        raise BadArgument(msg)


async def _delete(message, delay=None):
    if delay:
        await asyncio.sleep(delay)

    with suppress(discord.HTTPException):
        await message.delete()


class ChannelUserRole(IDConverter):
    """This will check to see if the provided argument is a channel, user, or
    role.

    Guidance code on how to do this from:

    """

    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.TextChannel, discord.Member, discord.Role]:
        guild = ctx.guild
        result = None
        id_match = self._get_id_match(argument)
        channel_match = re.match(r"<#([0-9]+)>$", argument)
        member_match = re.match(r"<@!?([0-9]+)>$", argument)
        role_match = re.match(r"<@&([0-9]+)>$", argument)
        for converter in ["channel", "role", "member"]:
            if converter == "channel":
                if match := id_match or channel_match:
                    channel_id = match.group(1)
                    result = guild.get_channel(int(channel_id))
                else:
                    result = discord.utils.get(guild.text_channels, name=argument)
            elif converter == "member":
                if match := id_match or member_match:
                    member_id = match.group(1)
                    result = guild.get_member(int(member_id))
                else:
                    result = guild.get_member_named(argument)
            elif converter == "role":
                if match := id_match or role_match:
                    role_id = match.group(1)
                    result = guild.get_role(int(role_id))
                else:
                    result = discord.utils.get(guild._roles.values(), name=argument)
            if result:
                break
        if not result:
            msg = f"{argument} is not a valid channel, user or role."
            raise BadArgument(msg)
        return result


class InviteBlocklist(commands.Cog):
    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=218773382617890828)
        self.config.register_guild(**GuildSettings().dict())
        self.locks = default_lock_cache()
        self.active_tasks = []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not message.guild:
            return

        settings = await self.get_guild_settings(message.guild.id)
        if settings.enabled:
            await self._handle_message_search(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Handle messages edited with links."""
        guild = payload.cached_message.guild if payload.cached_message else self.bot.get_guild(int(payload.data["guild_id"]))
        if guild is None:
            return
        chan = guild.get_channel(payload.channel_id)
        if chan is None:
            return
        if not payload.cached_message:
            return

        settings = await self.get_guild_settings(guild.id)
        if settings.enabled:
            try:
                msg = payload.cached_message
            except (discord.errors.Forbidden, discord.errors.NotFound):
                return
            await self._handle_message_search(msg)

    @alru_cache(maxsize=None)
    async def get_guild_settings(self, guild_id) -> GuildSettings:
        async with self.config.guild_from_id(guild_id).all() as _data:
            return GuildSettings(**_data)

    async def set_guild_settings(self, guild_id: int, settings: GuildSettings):
        async with self.config.guild_from_id(guild_id).all() as _data:
            _data.update(settings.dict())

        await checkpoint()
        self.get_guild_settings.cache_clear()

    async def check_immunity_list(self, message: discord.Message) -> bool:
        is_immune = False
        if not hasattr(message.author, "guild_permissions"):
            return
        if not message.guild:
            return
        guild: discord.Guild = message.guild
        settings = await self.get_guild_settings(guild.id)
        immunity_list = settings.immunity_list
        channel = message.channel
        if settings.ignore_mods and message.author.guild_permissions.manage_messages:
            is_immune = True

        if channel.id in immunity_list:
            is_immune = True
        if channel.category_id and channel.category_id in immunity_list:
            is_immune = True
        if message.author.id in immunity_list:
            is_immune = True
        for role in getattr(message.author, "roles", []):
            if role.is_default():
                continue
            if role.id in immunity_list:
                is_immune = True
        return is_immune

    @alru_cache(maxsize=None, ttl=90)
    async def fetch_cached_invite(self, code):
        key = f"cached_invite:{code}"

        r = None
        cached = await self.bot.redis.get(key)
        if cached:
            if cached := orjson.loads(cached):
                return SerialInvite(**cached)
            else:
                raise discord.NotFound(InvalidInvitePayload(), "Invalid invite")
        else:
            try:
                r: discord.Invite = await self.bot.fetch_invite(code)
                if r:
                    data = SerialInvite(max_age=r.max_age, code=r.code, guild=SerialGuild(name=r.guild.name, id=r.guild.id))
                    await self.bot.redis.set(key, data.json(), ex=30)
                    return data
            except discord.NotFound:
                await self.bot.redis.set(key, orjson.dumps(None), ex=30)
            raise discord.NotFound(InvalidInvitePayload(), "Invalid invite")

    async def _handle_message_search(self, message: discord.Message):
        if not hasattr(message.author, "guild_permissions"):
            return
        if await self.check_immunity_list(message) is True:
            return
        find = INVITE_RE.findall(message.clean_content)
        guild = message.guild
        settings = await self.get_guild_settings(guild.id)
        for i in find:
            try:
                invite = await self.fetch_cached_invite(i)
            except discord.NotFound:
                spawn_task(_delete(message, random.uniform(3, 4)), self.active_tasks)
                continue
            if invite.guild.id == message.guild.id:
                continue
            if invite.guild.id not in settings.whitelist:
                await _delete(message)

    @commands.group(name="inviteblock", aliases=["ibl", "inviteblocklist"])
    @commands.mod_or_permissions(manage_messages=True)
    async def invite_block(self, ctx: commands.Context) -> None:
        """Settings for managing invite link blocking."""

    @invite_block.group(name="whitelist", aliases=["allowlist", "wl", "al"])
    async def invite_whitelist(self, ctx: commands.Context) -> None:
        """Whitelist specific guild IDs from the blocker."""

    @invite_block.group(name="immunity", aliases=["immune"])
    async def invite_immunity(self, ctx: commands.Context) -> None:
        """Configure immune channels, roles, or users."""

    @invite_block.command()
    @commands.mod_or_permissions(manage_messages=True)
    async def ignoremods(self, ctx: commands.Context) -> None:
        """Toggle ON/OF whether invites sent from moderators are deleted."""
        settings = await self.get_guild_settings(ctx.guild.id)

        if settings.ignore_mods:
            await ctx.send(embed=make_e("I will delete invites **regardles** if the user is a moderator"))
            settings.ignore_mods = False
        else:
            await ctx.send(embed=make_e("I will delete invites **only if the user is not a moderator**ƒ"))
            settings.ignore_mods = True

        await self.set_guild_settings(ctx.guild.id, settings)

    @invite_block.command()
    @commands.mod_or_permissions(manage_messages=True)
    async def enable(self, ctx: commands.Context) -> None:
        """Automatically remove invites sent in chat to other servers."""
        settings = await self.get_guild_settings(ctx.guild.id)

        if not settings.enabled:
            await ctx.send(embed=make_e("Invite blocker enabled"))
            settings.enabled = True
        else:
            await ctx.send(embed=make_e("Invite blocker disabled"))
            settings.enabled = False

        await self.set_guild_settings(ctx.guild.id, settings)

    @invite_whitelist.command(name="add")
    async def add_to_whitelist(self, ctx: commands.Context, *invite_or_guild_id: Union[InviteConverter, ValidServerID]) -> None:
        """Add a guild ID or invite that will be exluded from the invite blocker."""
        guilds_blocked = []

        settings = await self.get_guild_settings(ctx.guild.id)

        for i in invite_or_guild_id:
            if isinstance(i, int):
                if i not in settings.whitelist:
                    settings.whitelist.append(i)
                    guilds_blocked.append(str(i))
            elif i.guild and i.guild.id not in settings.whitelist:
                settings.whitelist.append(i.guild.id)
                guilds_blocked.append(f"{i.guild.name} - {i.guild.id}")
        if guilds_blocked:
            await ctx.send(("Now Allowing invites from {guild}.").format(guild=humanize_list(guilds_blocked)))
        else:
            await ctx.send("None of the provided invite links or ID's are new.")

        await self.set_guild_settings(ctx.guild.id, settings)

    @invite_whitelist.command(name="remove", aliases=["del", "rem"])
    async def remove_from_whitelist(self, ctx: commands.Context, *invite_or_guild_id: Union[InviteConverter, ValidServerID]) -> None:
        """Remove a server ID or invite from the invite blocker."""
        guilds_blocked = []
        settings = await self.get_guild_settings(ctx.guild.id)

        for i in invite_or_guild_id:
            if isinstance(i, int):
                if i in settings.whitelist:
                    settings.whitelist.remove(i)
                    guilds_blocked.append(str(i))
            elif i.guild and i.guild.id in settings.whitelist:
                guilds_blocked.append(f"{i.guild.name} - {i.guild.id}")
                settings.whitelist.remove(i.guild.id)
        if guilds_blocked:
            await ctx.send(("Removed {guild} from whitelist.").format(guild=humanize_list(guilds_blocked)))
        else:
            await ctx.send("None of the provided invite links or guild ID's are currently allowed.")

        await self.set_guild_settings(ctx.guild.id, settings)

    @invite_whitelist.command(name="info")
    async def whitelist_info(self, ctx: commands.Context) -> None:
        """Show what guild ID's are in the invite link whitelist."""
        settings = await self.get_guild_settings(ctx.guild.id)

        whitelist = settings.whitelist
        msg = ("__Guild ID's Allowed__:\n{guilds}").format(guilds="\n".join(str(g) for g in whitelist))
        allow_list = await self.config.guild(ctx.guild).channel_user_role_allow()
        if allow_list:
            msg += ("__Allowed Channels, Users, and Roles:__\n{chan_user_roel}").format()
        for page in pagify(msg):
            await ctx.maybe_send_embed(page)

        self.get_guild_settings.cache_clear()

    @invite_immunity.command(name="add")
    async def add_to_invite_immunity(self, ctx: commands.Context, *channel_user_role: ChannelUserRole):
        """Add users, roles, or channels to be excluded from the invite blocker.

        (You can supply more than one of any at a time)

        """
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be allowed.")
        async with self.config.guild(ctx.guild).immunity_list() as whitelist:
            for obj in channel_user_role:
                if obj.id not in whitelist:
                    whitelist.append(obj.id)
        msg = "`{list_type}` added to the whitelist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

        self.get_guild_settings.cache_clear()

    @invite_immunity.command(name="remove", aliases=["del", "rem"])
    async def remove_from_invite_immunity(self, ctx: commands.Context, *channel_user_role: ChannelUserRole):
        """Remove users, roles, or channels from the inviteblockers exclusion list."""
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be whitelisted.")
        async with self.config.guild(ctx.guild).immunity_list() as whitelist:
            for obj in channel_user_role:
                if obj.id in whitelist:
                    whitelist.remove(obj.id)
        msg = "`{list_type}` removed from the whitelist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

        self.get_guild_settings.cache_clear()

    @invite_immunity.command(name="info")
    async def whitelist_context_info(self, ctx: commands.Context) -> None:
        """Show what channels, users, and roles are in the invite link whitelist."""
        msg = ("Invite immunity list for {guild}:\n").format(guild=ctx.guild.name)
        whitelist = await self.config.guild(ctx.guild).immunity_list()
        can_embed = ctx.channel.permissions_for(ctx.me).embed_links
        for obj_id in whitelist:
            obj = await ChannelUserRole().convert(ctx, str(obj_id))
            if isinstance(obj, discord.TextChannel):
                msg += f"{obj.mention}\n"
                continue
            msg += f"{obj.mention}\n" if can_embed else f"{obj.name}\n"
        for page in pagify(msg):
            await ctx.maybe_send_embed(page)

        self.get_guild_settings.cache_clear()
