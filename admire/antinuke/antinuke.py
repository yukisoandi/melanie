from __future__ import annotations

import asyncio
import contextlib
import enum
import textwrap
import time
from datetime import datetime
from typing import Optional

import arrow
import discord
import orjson
import regex as re
import tuuid
from melaniebot.core import checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import Context, check
from melaniebot.core.config import Config
from tornado.ioloop import PeriodicCallback
from xxhash import xxh3_64_hexdigest, xxh32_hexdigest

from melanie import (
    BaseModel,
    capturetime,
    checkpoint,
    default_lock_cache,
    footer_gif,
    get_redis,
    log,
    make_e,
    yesno,
)


class AntinukeLimitSurpassed(Exception):
    pass


ALLOWED_BOTS = (956298490043060265, 919089251298181181)


class VanityAntinukeProtectionEvent(BaseModel):
    guild_id: int
    target_vanity: str
    bad_vanity: str
    created_at: float
    confirm_key: str
    lock: str

    async def publish(self) -> None:
        redis = get_redis()
        await redis.publish("vanity_anti_events", self.json())

    async def wait_for_ack(self, max_wait: float = 5) -> bool | None:
        redis = get_redis()
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(max_wait):
                while True:
                    await asyncio.sleep(0.1)
                    if await redis.get(self.confirm_key):
                        return True


class LogActionType(enum.Enum):
    vanity = "Vanity Change"
    ban = "Mass ban"
    channel = "Channel Create/Delete"
    role = "Role Add/Delete"
    kick = "Mass Kick"
    webhook_mention = "Webhook Mention Spam"


class GuildSettings(BaseModel):
    log_channel_id: Optional[int]
    trusted_admins: list[int] = []
    vanity_an: bool = False
    bot_an: bool = False
    mention_webhook: bool = False
    ban_thres: int = 0
    kick_thres: int = 0
    emoji_thres: int = 0
    role_thres: int = 0
    channel_thres: int = 0


class CachedGuildRole(BaseModel):
    members: list[int] = []
    name: str
    id: int
    hoist: bool
    permissions: int
    mentionable: bool
    colour: int
    position: int

    @staticmethod
    def make_cache_key(role_id) -> str:
        return f"antinuke_cachedrole:{xxh32_hexdigest(str(role_id))}"

    @property
    def cache_key(self):
        return self.make_cache_key(self.id)

    async def set_cache(self) -> None:
        redis = get_redis()
        await redis.set(self.cache_key, orjson.dumps(self.dict()), ex=300)

    @classmethod
    async def fetch_cached(cls, role_id: int):  # sourcery skip: assign-if-exp
        redis = get_redis()
        key = CachedGuildRole.make_cache_key(role_id)
        data = await redis.get(key)
        return CachedGuildRole.parse_raw(data) if data else None

    @classmethod
    async def from_role(cls, role: discord.Role):
        ids = []
        for member in role.members:
            await asyncio.sleep(0)
            ids.append(member.id)

        return CachedGuildRole(
            id=role.id,
            members=ids,
            name=role.name,
            hoist=role.hoist,
            permissions=role.permissions.value,
            mentionable=role.mentionable,
            colour=role.color.value,
            position=role.position,
        )


class UserPassport(BaseModel):
    user_id: int
    guild_id: int
    created_at: int
    author_id: int

    @classmethod
    async def fetch(cls, guild_id, user_id):
        redis = get_redis()

        data = await redis.get(UserPassport.make_key(guild_id, user_id))
        if not data:
            return None

        data = orjson.loads(data)
        return UserPassport(**data)

    @staticmethod
    def make_key(guild_id, user_id) -> str:
        return f"antinuke_passport:{xxh3_64_hexdigest(f'{user_id}{guild_id}')}"

    def key(self):
        return self.make_key(self.guild_id, self.user_id)

    async def activate(self) -> None:
        redis = get_redis()

        await redis.set(self.key(), orjson.dumps(self.dict()), ex=300)


def is_owner():
    async def predicate(ctx):
        return ctx.author.id == 316026178463072268

    return commands.check(predicate)


class AntiNuke(commands.Cog):
    """antinuke."""

    def reqs_check(self):
        async def predicate(ctx: Context):
            settings = await self.get_guild_settings(ctx.guild)
            return bool(settings.log_channel_id)

        return check(predicate)

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.closed = False
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.guild_settings_cache = {}
        self.active_tasks = []
        self.locks = default_lock_cache()

        self.guild_callbacks: dict[int, PeriodicCallback] = {}

    async def track_kick(self, guild: discord.Guild, user: discord.User):
        settings = await self.get_guild_settings(guild)
        if not settings.ban_thres:
            return
        if user.id in settings.trusted_admins:
            return
        key = self.kick_action_key(guild.id, user.id)
        value = await self.bot.redis.incr(key)
        await self.bot.redis.expire(key, 3600)
        if value >= settings.ban_thres:
            msg = "User has surpassed the configured antinuke kick limit"
            raise AntinukeLimitSurpassed(msg)

    async def track_ban(self, guild: discord.Guild, user: discord.User, revert: bool = False):
        settings = await self.get_guild_settings(guild)
        if not settings.ban_thres:
            return
        if user.id in settings.trusted_admins:
            return
        key = self.ban_action_key(guild.id, user.id)
        if revert:
            value = await self.bot.redis.decr(key)
        else:
            value = await self.bot.redis.incr(key)
        await self.bot.redis.expire(key, 3600)
        if value >= settings.ban_thres:
            msg = "User has surpassed the configured antinuke ban limit"
            raise AntinukeLimitSurpassed(msg)

    def cog_unload(self) -> None:
        self.closed = True
        for t in self.active_tasks:
            t.cancel()
        for t in self.guild_callbacks.values():
            t.stop()

    async def refresh_guild_settings(self, guild) -> None:
        with contextlib.suppress(KeyError):
            del self.guild_settings_cache[guild.id]
        await self.get_guild_settings(guild)

    async def get_guild_settings(self, guild) -> GuildSettings:
        if guild.id not in self.guild_settings_cache:
            data = await self.config.guild(guild).all()
            self.guild_settings_cache[guild.id] = GuildSettings(**data)
        return self.guild_settings_cache[guild.id]

    async def log_event(
        self,
        guild: discord.Guild,
        date: datetime,
        action: LogActionType,
        user: discord.User,
        failed: bool,
        whitelisted: bool = False,
        error: str = None,
        extra: dict = None,
    ):
        settings = await self.get_guild_settings(guild)
        channel: discord.TextChannel = self.bot.get_channel(settings.log_channel_id)
        if not channel:
            return log.warning(f"{guild} has no log channel")

        if whitelisted:
            verb = "Authorized"
        elif failed:
            verb = "Unresolved"
        else:
            verb = "Resolved"
        desc = f"{action.value} {verb}"
        if failed:
            embed = make_e(desc, 3)
        elif whitelisted:
            embed = make_e(desc, "info")
        else:
            embed = make_e(desc, 1)

        if error:
            embed.add_field(name="error", value=error)
        if extra:
            for name, value in extra.items():
                embed.add_field(name=name, value=value)

        embed.add_field(name="User", value=f"{user} ({user.id})")
        embed.timestamp = date

        await channel.send(embed=embed)

    async def is_trusted_admin(self, ctx: commands.Context) -> bool:
        if ctx.author.id == ctx.guild.owner_id:
            return True

        if ctx.author.id in self.bot.owner_ids:
            return True
        settings = await self.get_guild_settings(ctx.guild)

        return ctx.author.id in settings.trusted_admins

    @checks.has_permissions(administrator=True)
    @commands.group(name="antinuke", aliases=["an"])
    async def antinuke(self, ctx: commands.Context) -> None:
        """Configure Melanie's antinuke."""

    @antinuke.command()
    async def log(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the log channel for all antinuke actions and changes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await self.refresh_guild_settings(ctx.guild)
        return await ctx.send(embed=make_e(f"I've set the log channel to {channel}"))

    @antinuke.command()
    async def passport(self, ctx: commands.Context, user: discord.User):
        """Passport a user or bot to bypass antinuke and anti-raid protections."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("Passports can only be created by trusted admins", 2))
        passport = UserPassport(user_id=user.id, guild_id=ctx.guild.id, created_at=time.time(), author_id=ctx.author.id)
        await passport.activate()
        return await ctx.send(embed=make_e(f"{user} has a passport valid for 60 minutes"))

    @antinuke.command()
    async def webhook(self, ctx: commands.Context):
        """Toggle monitoring and deleting webhooks that mass mention."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        mention_webhook: bool = await self.config.guild(ctx.guild).mention_webhook()

        if mention_webhook:
            conf, _msg = await yesno("Webhook protection is on", "Would you like to disable it?")
            if conf:
                await self.config.guild(ctx.guild).mention_webhook.set(False)
        else:
            conf, _msg = await yesno("Webhook protection is off", "Would you like to enable it?")
            if conf:
                await self.config.guild(ctx.guild).mention_webhook.set(True)
        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def botadd(self, ctx: commands.Context):
        """Toggle kicking of bots that are added without a passport."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        bot_an: bool = await self.config.guild(ctx.guild).bot_an()

        if bot_an:
            conf, _msg = await yesno("Bot add protection is on", "Would you like to disable it?")
            if conf:
                await self.config.guild(ctx.guild).bot_an.set(False)
        else:
            conf, _msg = await yesno("Bot add protection is off", "Would you like to enable it?")
            if conf:
                await self.config.guild(ctx.guild).bot_an.set(True)
        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def vanity(self, ctx: commands.Context):
        """Toggle vanity changes allowed by non whitelisted users."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        vanity_an: bool = await self.config.guild(ctx.guild).vanity_an()

        if vanity_an:
            conf, _msg = await yesno("Vanity protection is on", "Would you like to disable it?")
            if conf:
                await self.config.guild(ctx.guild).vanity_an.set(False)
        else:
            conf, _msg = await yesno("Vanity protection is off", "Would you like to enable it?")
            if conf:
                await self.config.guild(ctx.guild).vanity_an.set(True)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def channel(self, ctx: commands.Context, threshold: int):
        """The number of allowed channel deletions and creations under 60 minutes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        if threshold > 10:
            return await ctx.send(embed=make_e("The threshold must be lower than 10", 3))
        channel_thres: bool = await self.config.guild(ctx.guild).channel_thres()
        state = f"set to {channel_thres}" if channel_thres else "disabled"
        conf, _msg = await yesno(f"Channel protection is {state}", f"Would you like to set it to {threshold} ?")
        if conf:
            await self.config.guild(ctx.guild).channel_thres.set(threshold)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def emoji(self, ctx: commands.Context, threshold: int):
        """The number of allowed emoji deletions and creations under 60 minutes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        if threshold > 10:
            return await ctx.send(embed=make_e("The threshold must be lower than 10", 3))
        emoji_thres: bool = await self.config.guild(ctx.guild).emoji_thres()
        state = f"set to {emoji_thres}" if emoji_thres else "disabled"
        conf, _msg = await yesno(f"Emoji protection is {state}", f"Would you like to set it to {threshold} ?")
        if conf:
            await self.config.guild(ctx.guild).emoji_thres.set(threshold)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def role(self, ctx: commands.Context, threshold: int):
        """The number of allowed role deletions and creations under 60 minutes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        if threshold > 10:
            return await ctx.send(embed=make_e("The threshold must be lower than 10", 3))
        role_thres: bool = await self.config.guild(ctx.guild).role_thres()
        state = f"set to {role_thres}" if role_thres else "disabled"
        conf, _msg = await yesno(f"Role protection is {state}", f"Would you like to set it to {threshold} ?")
        if conf:
            await self.config.guild(ctx.guild).role_thres.set(threshold)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def kick(self, ctx: commands.Context, threshold: int):
        """Set the kick threshold for 60 minutes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))
        if threshold > 10:
            return await ctx.send(embed=make_e("The threshold must be lower than 10", 3))
        kick_thres: bool = await self.config.guild(ctx.guild).kick_thres()
        state = f"set to {kick_thres}" if kick_thres else "disabled"
        conf, _msg = await yesno(f"Kick protection is {state}", f"Would you like to set it to {threshold} ?")
        if conf:
            await self.config.guild(ctx.guild).kick_thres.set(threshold)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def ban(self, ctx: commands.Context, threshold: int):
        """Set the ban threshold for 60 minutes."""
        if not await self.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("This setting can only be updated by a trusted admin", 2))

        if threshold > 10:
            return await ctx.send(embed=make_e("The threshold must be lower than 10", 3))
        ban_thres: bool = await self.config.guild(ctx.guild).ban_thres()
        state = f"set to {ban_thres}" if ban_thres else "disabled"
        conf, _msg = await yesno(f"Ban protection is {state}", f"Would you like to set it to {threshold} ?")
        if conf:
            await self.config.guild(ctx.guild).ban_thres.set(threshold)

        await self.refresh_guild_settings(ctx.guild)

    @antinuke.command()
    async def trust(self, ctx: commands.Context, member: discord.Member):
        """Add an antinuke admin."""
        if not await self.is_trusted_admin(ctx) and ctx.author.id not in self.bot.owner_ids and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=make_e("This setting can only be updated by the guild owner", 2))

        async with self.config.guild(ctx.guild).all(acquire_lock=False) as data:
            if member.id in data["trusted_admins"]:
                confirmed, _msg = await yesno(f"{member} is already a trusted admin", "should I remove their trust?")
                if confirmed:
                    data["trusted_admins"].remove(member.id)
            else:
                confirmed, _msg = await yesno(f"Are you sure you want to add {member} as a trusted admin?")
                if confirmed:
                    data["trusted_admins"].append(member.id)

        await self.refresh_guild_settings(ctx.guild)

    async def remove_left_admins(self, guild: discord.Guild):
        async with self.config.guild(guild).all(acquire_lock=False) as _settings:
            settings = GuildSettings(**_settings)
            for mid in list(settings.trusted_admins):
                if not guild.get_member(mid):
                    settings.trusted_admins.remove(mid)
            _settings.update(settings.dict())

    @antinuke.command()
    async def settings(self, ctx: commands.Context):
        """Display the servers antinuke settings."""
        await self.remove_left_admins(ctx.guild)
        await self.refresh_guild_settings(ctx.guild)
        settings = await self.get_guild_settings(ctx.guild)
        embed = discord.Embed(title="melanie antinuke settings")
        embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
        log_channel = self.bot.get_channel(settings.log_channel_id)
        embed.add_field(name="webhook protection", value="On" if settings.mention_webhook else "Disabled")
        embed.add_field(name="bot add protection", value="On" if settings.bot_an else "Disabled")
        embed.add_field(name="log channel", value=f"{log_channel} ({log_channel.mention}) " if settings.log_channel_id else "unset")
        embed.add_field(name="vanity protection", value="On" if settings.vanity_an else "Disabled")
        embed.add_field(
            name="trusted admins",
            value="\n".join(str(self.bot.get_user(u)) for u in settings.trusted_admins) if settings.trusted_admins else "None",
        )
        embed.add_field(name="channel add/delete threshold", value=settings.channel_thres or "Disabled")
        embed.add_field(name="ban threshold", value=settings.ban_thres or "Disabled")

        embed.add_field(name="kick threshold", value=settings.kick_thres or "Disabled")
        embed.add_field(name="emoji add/delete threshold", value=settings.emoji_thres or "Disabled")
        embed.add_field(name="role add/delete threshold", value=settings.role_thres or "Disabled")
        return await ctx.send(embed=embed)

    @staticmethod
    def channel_action_key(guild_id, user_id) -> str:
        return f"antinuke_channel:{xxh3_64_hexdigest( f'{guild_id}{user_id}')}"

    @staticmethod
    def kick_action_key(guild_id, user_id) -> str:
        return f"antinuke_kick:{xxh3_64_hexdigest( f'{guild_id}{user_id}')}"

    @staticmethod
    def ban_action_key(guild_id, user_id) -> str:
        return f"antinuke_ban:{xxh3_64_hexdigest( f'{guild_id}{user_id}')}"

    @staticmethod
    def role_action_key(guild_id, user_id) -> str:
        return f"antinuke_role:{xxh3_64_hexdigest( f'{guild_id}{user_id}')}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild: discord.Guild = message.guild

        if not guild:
            return

        settings = await self.get_guild_settings(guild)

        if not settings.mention_webhook:
            return

        if not message.webhook_id:
            return

        await checkpoint()
        should_del = bool(message.mention_everyone)
        if not should_del and (matched_ids := re.findall(r"<@&([0-9]+)>$", message.content)):
            for match in matched_ids:
                if role := guild.get_role(int(match)):
                    total_members = len(role.members)
                    if len(total_members) > 100:
                        should_del = True

        if should_del:
            lock = self.locks[f"whaudit:{message.webhook_id}"]
            try:
                async with asyncio.timeout(0.0001):
                    await lock.acquire()
            except TimeoutError:
                return

            else:
                try:
                    webhooks = await guild.webhooks()
                    for hook in webhooks:
                        if hook.id == message.webhook_id:
                            await hook.delete(reason="Mention spam")
                            extra = {"webhook_id": message.webhook_id, "content": textwrap.shorten(message.content, 400), "channel": str(message.channel)}
                            return await self.log_event(
                                guild=guild,
                                date=message.created_at,
                                action=LogActionType.webhook_mention,
                                user=message.author,
                                failed=False,
                                extra=extra,
                            )
                finally:
                    lock.release()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild: discord.Guild = member.guild
        error = None
        redis = get_redis()
        settings = await self.get_guild_settings(guild)

        if not settings.kick_thres:
            return

        logs = await member.guild.audit_logs(action=discord.AuditLogAction.kick, limit=1).flatten()
        if not logs:
            return
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        target: discord.User = entry.target
        if target.id != member.id:
            return
        key = self.role_action_key(guild.id, perp.id)
        extra = {"User kicked": str(member), "ID": member.id}
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            return
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.kick, user=perp, failed=False, whitelisted=True, extra=extra)
        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.kick_thres:
            if perp_member := guild.get_member(perp.id):
                try:
                    if perp_member.id in self.bot.owner_ids:
                        log.warning(f"Not banning {perp_member} in owner ids")
                    else:
                        await perp_member.ban(reason="Surpassed kick threshold")
                except discord.HTTPException as e:
                    error = f"Error 1: {error}\n Error 2: {e}" if error else str(e)

            if error:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.kick, user=perp, failed=True, error=error, extra=extra)
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.kick, user=perp, failed=False, extra=extra)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild: discord.Guild = role.guild
        error = None
        redis = get_redis()
        settings = await self.get_guild_settings(guild)
        if not settings.role_thres:
            return
        logs = await role.guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=1).flatten()
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        key = self.role_action_key(guild.id, perp.id)
        extra = {"Role": str(role), "Action": "Create"}
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            cmd_key = f"role_create:{role.id}"
            data = await redis.get(cmd_key)
            if not data:
                return
            else:
                data = orjson.loads(data)
            user = self.bot.get_user(data)
            if user:
                log.warning(f"{role} created via cmd by {user}")
                perp = user
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.role, user=perp, failed=False, whitelisted=True, extra=extra)

        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.role_thres:
            if perp_member := guild.get_member(perp.id):
                if perp_member.id in self.bot.owner_ids:
                    log.warning(f"Not banning {perp_member} in owner ids")
                else:
                    try:
                        await perp_member.ban(reason="Too many role deletions or adds")
                    except discord.HTTPException as e:
                        error = str(e)
            if error:
                await self.log_event(
                    guild=guild,
                    date=entry.created_at,
                    action=LogActionType.role,
                    user=perp,
                    failed=True,
                    whitelisted=False,
                    extra=extra,
                    error=error,
                )
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.role, user=perp, failed=False, extra=extra)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild: discord.Guild = role.guild
        error = None
        redis = get_redis()
        cached_role = await CachedGuildRole.fetch_cached(role.id)
        settings = await self.get_guild_settings(guild)
        if not settings.role_thres:
            return

        logs = await role.guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=1).flatten()
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        key = self.role_action_key(guild.id, perp.id)
        member_cnt = len(cached_role.members) if cached_role else "Unknown"
        extra = {"Role": str(role), "Members": member_cnt, "Action": "Delete", "Cached": "Yes" if cached_role else "Missing"}
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            cmd_key = f"role_delete:{role.id}"
            data = await redis.get(cmd_key)
            if not data:
                return
            else:
                data = orjson.loads(data)
            user = self.bot.get_user(data)
            if user:
                log.warning(f"{role} deleted via cmd by {user}")
                perp = user
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.role, user=perp, failed=False, whitelisted=True, extra=extra)

        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.role_thres:
            if perp_member := guild.get_member(perp.id):
                if perp_member.id in self.bot.owner_ids:
                    log.warning(f"Not banning {perp_member} in owner ids")
                else:
                    try:
                        await perp_member.ban(reason="Too many role deletions or adds")
                    except discord.HTTPException as e:
                        error = str(e)
            if cached_role:
                new_role: discord.Role = await guild.create_role(
                    reason="Anti nuke role re-create",
                    hoist=cached_role.hoist,
                    mentionable=cached_role.mentionable,
                    color=cached_role.colour,
                    permissions=discord.Permissions(permissions=cached_role.permissions),
                    name=cached_role.name,
                )
                await new_role.edit(position=cached_role.position)
                for mid in cached_role.members:
                    if member := guild.get_member(mid):
                        await member.add_roles(new_role, reason="Readding role after nuke deletion")

            if error:
                await self.log_event(
                    guild=guild,
                    date=entry.created_at,
                    action=LogActionType.role,
                    user=perp,
                    failed=True,
                    whitelisted=False,
                    extra=extra,
                    error=error,
                )
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.role, user=perp, failed=False, extra=extra)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        current_time = time.time()
        settings = await self.get_guild_settings(after)
        guild: discord.Guild = self.bot.get_guild(after.id)
        if not settings.vanity_an:
            return
        before_vanity = None
        with capturetime(f"looking for vanity change @ {guild.name}"), contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(5):
                while not before_vanity:
                    logs: list[discord.AuditLogEntry] = await before.audit_logs(action=discord.AuditLogAction.guild_update, limit=1).flatten()
                    for entry in logs:
                        entry: discord.AuditLogEntry = logs[0]
                        audit_time: float = arrow.get(entry.created_at).timestamp()
                        if audit_time < current_time:
                            await asyncio.sleep(0.08)
                            continue
                        perp: discord.User = entry.user
                        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
                            return
                        if await self.bot.redis.get(f"antinuke_tessa:{perp.id}"):
                            log.info("Ignoring an antinuke update event in network {}", entry)
                            continue
                        try:
                            before_vanity = str(entry.changes.before.vanity_url_code)
                            after_vanity = str(entry.changes.after.vanity_url_code)
                            extra = {"Old vanity": before_vanity, "New Vanity": after_vanity}
                        except AttributeError:
                            continue

        if not before_vanity:
            return log.warning("No vanity change detected - ignoring")
        key = f"{guild.id}{perp.id}{before_vanity}{after_vanity}{entry.created_at.timestamp()}"
        event_key = f"an_vanity_event:{xxh3_64_hexdigest(key)}"
        if await self.bot.redis.get(event_key):
            return log.warning("Event already handled")
        if before_vanity == after_vanity:
            return
        if perp.id == guild.owner_id:
            return log.info("Ignoring a vanity change made by owner @ {}", guild)
        await self.bot.redis.set(event_key, 1)
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.vanity, user=perp, failed=False, whitelisted=True, extra=extra)

        log.warning(f"Vanity changed by non trusted user {perp} @ {after} before: {before_vanity} after: {after_vanity}")
        error = None

        vanity_event = VanityAntinukeProtectionEvent(
            guild_id=guild.id,
            target_vanity=before_vanity,
            bad_vanity=after_vanity,
            created_at=time.time(),
            confirm_key=tuuid.tuuid(),
            lock=tuuid.tuuid(),
        )

        await vanity_event.publish()
        ack = await vanity_event.wait_for_ack()
        if ack:
            log.success("Vanity ack retrived! - Event submitted {}", vanity_event)

        else:
            error = "Confirmation from the worker was never retrived. Please report this"

        if perp_member := guild.get_member(perp.id):
            try:
                if perp_member.id in self.bot.owner_ids:
                    log.warning(f"Not banning {perp_member} in owner ids")
                else:
                    await perp_member.ban(reason=f"Changed the vanity to {after_vanity}")
            except discord.HTTPException as e:
                error = f"Error 1: {error}\n Error 2: {e}" if error else str(e)

        if error:
            await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.vanity, user=perp, failed=True, error=error, extra=extra)
        else:
            await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.vanity, user=perp, failed=False, extra=extra)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.id in ALLOWED_BOTS:
            return
        settings = await self.get_guild_settings(member.guild)
        if settings.bot_an and member.bot:
            passport = await UserPassport.fetch(member.guild.id, member.id)
            if not passport:
                await member.kick(reason="Bot did not have an active passport")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        settings = await self.get_guild_settings(guild)
        redis = get_redis()
        error = None
        if not settings.ban_thres:
            return

        logs: list[discord.AuditLogEntry] = await guild.audit_logs(action=discord.AuditLogAction.ban, limit=1).flatten()
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            return
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.ban, user=perp, failed=False, whitelisted=True)

        key = self.ban_action_key(guild.id, perp.id)
        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.ban_thres:
            if perp_member := guild.get_member(perp.id):
                try:
                    if perp_member.id in self.bot.owner_ids:
                        log.warning(f"Not banning {perp_member} in owner ids")
                    else:
                        await perp_member.ban(reason="Surpassed ban threshold")
                except discord.HTTPException as e:
                    error = f"Error 1: {error}\n Error 2: {e}" if error else str(e)

            if error:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.ban, user=perp, failed=True, error=error)
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.ban, user=perp, failed=False)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild: discord.Guild = channel.guild

        settings = await self.get_guild_settings(guild)
        redis = get_redis()
        error = None
        if not settings.channel_thres:
            return
        logs: list[discord.AuditLogEntry] = await guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=1).flatten()
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            return
        if perp.id in settings.trusted_admins:
            return await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.channel, user=perp, failed=False, whitelisted=True)

        key = self.channel_action_key(guild.id, perp.id)
        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.channel_thres:
            await channel.delete(reason="Surpassed channel threshold")
            if perp_member := guild.get_member(perp.id):
                try:
                    if perp_member.id in self.bot.owner_ids:
                        log.warning(f"Not banning {perp_member} in owner ids")
                    else:
                        await perp_member.ban(reason="Surpassed channel threshold")
                except discord.HTTPException as e:
                    error = f"Error 1: {error}\n Error 2: {e}" if error else str(e)

            if error:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.channel, user=perp, failed=True, error=error)
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.channel, user=perp, failed=False)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild: discord.Guild = channel.guild

        settings = await self.get_guild_settings(guild)
        redis = get_redis()
        error = None
        if not settings.channel_thres:
            return
        logs: list[discord.AuditLogEntry] = await guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=1).flatten()
        entry: discord.AuditLogEntry = logs[0]
        perp: discord.User = entry.user
        extra = {"Channel Name": str(channel), "Channel ID": channel.id}
        if perp.id in (self.bot.user.id, 919089251298181181, 919089251298181181, 956298490043060265):
            return
        if perp.id in settings.trusted_admins:
            return await self.log_event(
                guild=guild,
                date=entry.created_at,
                action=LogActionType.channel,
                user=perp,
                failed=False,
                whitelisted=True,
                extra=extra,
            )

        key = self.channel_action_key(guild.id, perp.id)
        value = await redis.incr(key)
        await redis.expire(key, 3600)
        if value >= settings.channel_thres:
            if perp_member := guild.get_member(perp.id):
                try:
                    if perp_member.id in self.bot.owner_ids:
                        log.warning(f"Not banning {perp_member} in owner ids")
                    else:
                        await perp_member.ban(reason="Surpassed channel threshold")
                except discord.HTTPException as e:
                    error = f"Error 1: {error}\n Error 2: {e}" if error else str(e)

            if error:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.channel, user=perp, failed=True, error=error, extra=extra)
            else:
                await self.log_event(guild=guild, date=entry.created_at, action=LogActionType.channel, user=perp, failed=False, extra=extra)
