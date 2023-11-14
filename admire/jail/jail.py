from __future__ import annotations

import asyncio
import copy
import time
from collections import defaultdict

import discord
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from xxhash import xxh32_hexdigest

from melanie import BaseModel, footer_gif, make_e, yesno


class GuildSettings(BaseModel):
    jail_role_id: int = None
    jail_channel_id: int = None
    auto_purge_jail: bool = True

    @classmethod
    async def load(cls, config: Config, guild: discord.Guild):
        data = await config.guild(guild).all()
        return cls(**data)


class MemberSettings(BaseModel):
    saved_role_ids: list[int] = None
    jailed_by: str = None
    jailed_time: int = None
    jailed_reason: str = None

    @classmethod
    async def load(cls, config: Config, member: discord.Member):
        data = await config.member(member).all()
        return cls(**data)


class Jail(commands.Cog):
    """Jail bad users."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.setup_locks = defaultdict(asyncio.Lock)
        self.jail_locks = defaultdict(asyncio.Lock)

    async def ensure_jailed_role_noperms(self, role: discord.Role) -> None:
        with log.catch(reraise=True):
            no_perms = discord.Permissions.none()
            if role > role.guild.me.top_role:
                return
            await role.edit(permissions=no_perms)

    async def ensure_jailed_permissions(self, channel: discord.abc.GuildChannel) -> None:
        with log.catch(reraise=True):
            settings = await GuildSettings.load(self.config, channel.guild)
            if not settings.jail_role_id:
                return

            guild: discord.Guild = channel.guild
            jail_role: discord.Role = guild.get_role(settings.jail_role_id)

            if not jail_role:
                return
            if channel.id == settings.jail_channel_id:
                await channel.set_permissions(
                    jail_role,
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True,
                    reason="Setting permissions required for Jail",
                )
            else:
                await channel.set_permissions(
                    jail_role,
                    read_messages=False,
                    send_messages=False,
                    add_reactions=False,
                    read_message_history=False,
                    reason="Setting permissions required for Jail",
                )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        with log.catch(reraise=True):
            await asyncio.sleep(0.2)
            guild: discord.Guild = member.guild
            member_settings = await MemberSettings.load(self.config, member)
            if not member_settings.jailed_time:
                return  # member not jailed

            settings = await GuildSettings.load(self.config, guild)
            jail_role: discord.Role = guild.get_role(settings.jail_role_id)
            if not jail_role:
                return
            to_remove = []

            for r in member.roles:
                r: discord.Role
                if r != guild.default_role and not r.managed:
                    to_remove.append(r)

            if to_remove:
                await member.remove_roles(*to_remove, reason="User is jailed")

            await member.add_roles(jail_role, reason="User was jailed when they left the server")

    @commands.has_permissions(kick_members=True)
    @commands.guild_only()
    @commands.group(name="jail", aliases=["cage"], invoke_without_command=True)
    async def jail(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        lock_key = xxh32_hexdigest(f"{ctx.guild.id}{member.id}")
        with log.catch(reraise=True):
            try:
                async with asyncio.timeout(0.0001):
                    await self.jail_locks[lock_key].acquire()
            except TimeoutError:
                return
            try:
                if ctx.guild.me.top_role <= member.top_role:
                    return await ctx.send(embed=make_e(f"My top role must be below {member.mention}'s top role"))
                if ctx.author.top_role <= member.top_role and ctx.author.id not in self.bot.owner_ids:
                    return await ctx.send(embed=make_e("You may only jail members whos top role is lower than yours", status=2))
                guild: discord.Guild = member.guild
                audit_reason = f"Member was jailed by {ctx.author} ({ctx.author.id})"
                settings = await GuildSettings.load(self.config, guild)
                async with asyncio.timeout(300):
                    async with ctx.typing():
                        if not settings.jail_channel_id or not settings.jail_role_id:
                            return await ctx.send(embed=make_e("Jail isn't setup yet! Run ;jail setup and re-jail the member", status=3))
                        jail_role = guild.get_role(settings.jail_role_id)
                        jail_channel = guild.get_channel(settings.jail_channel_id)

                        if not jail_role or not jail_channel:
                            return await ctx.send(embed=make_e("The jail channel or role was deleted. Run ;jail setup and rejail the member", status=3))
                        if jail_role in member.roles:
                            return await ctx.send(embed=make_e("It looks like this member is already jailed", status=2))

                        if jail_role > guild.me.top_role:
                            return await ctx.send(
                                embed=make_e(
                                    "My top role must be below the jail role. Please move me to the top of the role list and place the jail role directly below me.",
                                    status=3,
                                ),
                            )

                        async with self.config.member(member).all() as member_conf:
                            member_settings = MemberSettings()
                            member_settings.jailed_by = f"{ctx.author} ({ctx.author.id})"
                            member_settings.jailed_time = int(time.time())
                            to_remove = []
                            for r in member.roles:
                                r: discord.Role
                                if r != guild.default_role and not r.managed and not r.is_premium_subscriber():
                                    to_remove.append(r)
                            member_settings.saved_role_ids = [r.id for r in to_remove]
                            member_settings.jailed_reason = reason

                            try:
                                await member.remove_roles(*to_remove, reason=audit_reason)
                                await member.add_roles(jail_role, reason=audit_reason)
                            except discord.Forbidden:
                                await ctx.send(
                                    embed=make_e("Received a permission error from Discord. Please make sure I have the correct permissions to jail users", 3),
                                )
                            member_conf.update(member_settings.dict())
                            return await ctx.send(embed=make_e(f" Jailed {member.mention}!"))
            finally:
                self.jail_locks[lock_key].release()

    @commands.max_concurrency(1, commands.BucketType.guild)
    @checks.has_permissions(kick_members=True)
    @commands.command()
    async def unjail(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        async with ctx.typing(), asyncio.timeout(60):
            guild: discord.Guild = ctx.guild
            settings = await GuildSettings.load(self.config, ctx.guild)
            if not settings.jail_role_id:
                return await ctx.send(embed=make_e("Jail has not been setup! Setup jail and rerun the cmd", status=2))
            jail_role = guild.get_role(settings.jail_role_id)
            if not jail_role:
                return await ctx.send(embed=make_e("Jail role was deleted! You'll have to manually free this user"))
            audit_reason = f"Unjail requested by {ctx.author} ({ctx.author.id})"
            async with self.config.member(member).all(acquire_lock=False) as member_conf:
                member_settings = MemberSettings(**member_conf)
                await member.remove_roles(jail_role, reason=audit_reason)
                to_add = []
                if member_settings.saved_role_ids:
                    for rid in member_settings.saved_role_ids:
                        r: discord.Role = guild.get_role(rid)
                        if r and not r.managed and not r.is_integration():
                            to_add.append(r)
                if to_add:
                    await member.add_roles(*to_add, reason=audit_reason)
        await ctx.send(embed=make_e(f"{member.mention} was unjailed and role restored."))
        await self.config.member(member).clear()
        if settings.auto_purge_jail:
            if jail_channel := guild.get_channel(settings.jail_channel_id):
                new_ctx = copy.copy(ctx)
                new_ctx.channel = jail_channel
            await new_ctx.invoke(self.bot.get_command("purge all"), search=500)

    @checks.has_permissions(administrator=True)
    @jail.command("purge")
    async def jail_purge(self, ctx: commands.Context) -> None:
        """Toggle whether the jail is auto purged when a member is unjailed."""
        with log.catch(reraise=True):
            async with self.config.guild(ctx.guild).all() as guild_conf:
                settings = GuildSettings(**guild_conf)

                if settings.auto_purge_jail:
                    confirmed, _msg = await yesno("I'm set to purge the jail after a member is unjailed. Do you wish to disable this?")
                    if not confirmed:
                        return

                    else:
                        settings.auto_purge_jail = False

                else:
                    confirmed, _msg = await yesno("Do you wish to turn on jail auto purge?")
                    if not confirmed:
                        return

                    else:
                        settings.auto_purge_jail = True

                guild_conf.update(settings.dict())

    @checks.has_permissions(administrator=True)
    @jail.command("setup")
    async def jail_setup(self, ctx: commands.Context):
        """Interactive setup of Melanie's jail feature.

        Creates the necessary role and ensures channel permissions are
        set OK

        """
        with log.catch(reraise=True):
            async with asyncio.timeout(90):
                async with ctx.typing():
                    async with self.config.guild(ctx.guild).all() as guild_settings:
                        guild: discord.Guild = ctx.guild
                        settings = await GuildSettings.load(self.config, ctx.guild)
                        confirmed, _msg = await yesno("I'm going to create (or update) the necessary role and channel permissions for Jail", "is this ok?")
                        if not confirmed:
                            return
                        jail_role = guild.get_role(settings.jail_role_id)
                        if not jail_role:
                            jail_role = await guild.create_role(name="jailed ⚠️", mentionable=True, reason="Jail feature setup", colour=0xE8C34A)
                            await asyncio.sleep(0.2)
                            jail_pos = guild.me.top_role.position - 2
                            await jail_role.edit(position=jail_pos)

                        settings.jail_role_id = jail_role.id

                        jail_channel = guild.get_channel(settings.jail_channel_id)

                        if not jail_channel:
                            overwrites = {
                                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                                guild.me: discord.PermissionOverwrite(read_messages=True),
                                jail_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
                            }

                            jail_channel = await guild.create_text_channel(
                                name="jail",
                                slowmode_delay=1,
                                topic="you're jailed until staff frees you. ",
                                position=0,
                                overwrites=overwrites,
                            )

                        settings.jail_channel_id = jail_channel.id

                        guild_settings.update(**settings.dict())
                    # CONFIG LOCK EXIT
                    for ch in guild.channels:
                        await self.ensure_jailed_permissions(ch)

                    embed = discord.Embed()
                    embed.color = 0x55FF55
                    embed.title = "Jail setup complete!"
                    embed.description = f"Jail channel: {jail_channel.mention}\nJail role: {jail_role.mention}\n\nNew channels will automatically have the necessary permissions applied and I'll rejail users who leave the server while jailed.\n\nYou may rename the jail channel and role"
                    embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                    return await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self.ensure_jailed_permissions(channel)
