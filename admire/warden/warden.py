from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from functools import partial

import arrow
import discord
from aiomisc.periodic import PeriodicCallback
from aiomisc.utils import cancel_tasks
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config

from melanie import BaseModel, checkpoint, log, make_e, spawn_task, yesno
from melanie.vendor.disputils import BotConfirmation

warn = log.warning


class GuildSettings(BaseModel):
    hardbans_set: dict = {}
    vanity_guard_enabled: bool = False
    vanity_string: str = None
    spam_join_max_joins: int = None
    spam_join_timespam: int = None
    no_avatar_kick: bool = False
    delete_welcome_on_leave: bool = False
    welcome_channel: int = None
    kick_scheduler_on: bool = False


class ChannelSettings(BaseModel):
    purge_interval: int = None


class Warden(commands.Cog):
    """Warden."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=813636489074049055, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.config.register_channel(**ChannelSettings().dict())

        self.error_index = {}
        self.purge_locks = defaultdict(asyncio.Lock)
        self.invite_locks = defaultdict(asyncio.Lock)
        self.kick_sems = defaultdict(partial(asyncio.Semaphore, 3000))
        self.active_tasks = []
        self.closed = False
        self.settings_cache = {}
        self.purge_cbs: dict[int, PeriodicCallback] = {}
        self.cleanup_cb = PeriodicCallback(self.cleanup_init)
        self.cleanup_cb.start(90)
        spawn_task(self.init_purge_tasks(), self.active_tasks)

    def cog_unload(self) -> None:
        self.closed = True
        self.cleanup_cb.stop(True)
        cancel_tasks(self.active_tasks)
        for cb in self.purge_cbs.values():
            cb.stop(True)

    async def init_purge_tasks(self):
        await self.bot.waits_uptime_for(90)
        all_channels = await self.config.all_channels()
        for cid, data in all_channels.items():
            await checkpoint()
            channel: discord.TextChannel = self.bot.get_channel(cid)
            if not channel:
                continue
            settings = ChannelSettings(**data)
            if not settings.purge_interval:
                continue
            if cid in self.purge_cbs:
                continue
            self.purge_cbs[cid] = PeriodicCallback(self.do_channel_purge, cid)
            self.purge_cbs[cid].start(random.uniform(70, 90), delay=random.randint(1, 5))

    async def cleanup_init(self) -> None:
        await self.bot.waits_uptime_for(60)
        tried = {}
        all_guilds = await self.config.all_guilds()
        for guild_id, hardbans in all_guilds.items():
            if hardbans and hardbans["hardbans_set"]:
                for k, v in hardbans["hardbans_set"].items():
                    guild: discord.Guild = self.bot.get_guild(int(guild_id))
                    if not guild:
                        continue

                    if not guild.me.guild_permissions.administrator:
                        continue

                    if member := guild.get_member(int(k)):
                        if member.id in tried:
                            continue

                        member: discord.Member
                        if member.top_role > guild.me.top_role:
                            tried[member.id] = time.time()
                            continue
                        log.warning("Init hardban: {} @ {}", member, guild)
                        author = v["ban_author"]
                        reason = str(
                            f"User cannot be unbanned without server owner approval. Hard ban requested for this user by {author['name']} ({author['id']}).",
                        )
                        try:
                            await member.ban(reason=reason)
                        except discord.HTTPException:
                            tried[member.id] = time.time()
                            continue

    async def do_channel_purge(self, channel_id: int) -> None:
        channel: discord.TextChannel = self.bot.get_channel(channel_id)
        if not channel:
            return
        interval: int = await self.config.channel(channel).purge_interval()
        if not interval:
            return
        queue = asyncio.Queue(200)

        async def issue_del():
            pocket: list[discord.Message] = []
            while True:
                msg = await queue.get()
                queue.task_done()
                if msg == "DONE":
                    if pocket:
                        await channel.delete_messages(pocket)
                    return
                pocket.append(msg)
                if len(pocket) == 100:
                    await channel.delete_messages(pocket)
                    pocket = []

        spawn_task(issue_del(), self.active_tasks)
        break_ts = arrow.utcnow().shift(hours=-interval).timestamp()
        async for m in channel.history(oldest_first=True, after=arrow.utcnow().shift(days=-13.5).naive):
            m: discord.Message
            if m.created_at.timestamp() > break_ts:
                break
            if m.pinned:
                continue
            await queue.put(m)
        await queue.put("DONE")
        await queue.join()

    @commands.command()
    @commands.max_concurrency(1, commands.BucketType.guild)
    @checks.has_permissions(administrator=True)
    async def autopurge(self, ctx: commands.Context, hours: int):
        """Passive autopurge of a channel.

        Configures melanie to delete all messages in the channel older
        than the provided number of hours.

        """
        channel: discord.TextChannel = ctx.channel

        if hours == 0:
            await self.config.channel(channel).clear()

            self.purge_cbs[channel.id].stop(True)

            return await ctx.send(embed=make_e("The autopurge from this channel was removed", 2))

        await self.config.channel(channel).purge_interval.set(hours)
        if channel.id in self.purge_cbs:
            self.purge_cbs[channel.id].stop(True)
            await checkpoint()
        self.purge_cbs[channel.id] = PeriodicCallback(self.do_channel_purge, channel.id)
        self.purge_cbs[channel.id].start(random.uniform(70, 90), delay=random.uniform(1, 10))

        return await ctx.send(
            embed=make_e(
                f"Configured autopurge for this channel to purge messages older than {hours} hours",
                tip="purges will be ran approximately every 10 minutes",
            ),
        )

    @commands.guild_only()
    @commands.group(name="warden")
    async def warden(self, ctx: commands.Context) -> None:
        """Warden - Advanced moderation tools to control your server."""

    @warden.command(aliases=["invitepurge", "staleinvites"])
    @commands.max_concurrency(1, commands.BucketType.guild)
    @checks.has_permissions(administrator=True)
    async def inviteprune(self, ctx: commands.Context):
        """Remove invites with 0 uses."""
        guild: discord.Guild = ctx.guild

        invites = await guild.invites()

        not_used = [i for i in invites if i.uses == 0]
        if not not_used:
            return await ctx.send(embed=make_e("There are no stale invites!"))

        confirmed, _msg = await yesno(f"There are {len(not_used)} invites with 0 uses.", "Can I delete them?")

        if not confirmed:
            return

        status = await ctx.send(embed=make_e(f"Deleted 0/{len(not_used)}"))

        try:
            i: discord.Invite
            total_errors = 0

            for idx, i in enumerate(not_used, start=1):
                try:
                    async with asyncio.timeout(3):
                        try:
                            await i.delete()
                            log.info(f"Deleted {i} from {ctx.guild} OK")
                            await status.edit(embed=make_e(f"Deleted {idx}/{len(not_used)}"))
                        except discord.HTTPException:
                            total_errors += 1

                            if total_errors > 9:
                                log.error(f"Bailing on {ctx.guild}")
                                return await ctx.send(embed=make_e("Bailing on the request to delete invites. Too many errors from Discord", 2))
                except TimeoutError:
                    total_errors += 1
                    log.warning(f"Timeout for {i}")
                    if total_errors > 9:
                        log.error(f"Bailing on {ctx.guild}")
                        return await ctx.send(embed=make_e("Bailing on the request to delete invites. Too many errors from Discord", 2))

                await asyncio.sleep(3)

            return await ctx.send(embed=make_e("Stale invites deleted!"))

        finally:
            await status.delete()

    @warden.command()
    @checks.has_permissions(administrator=True)
    async def checkav(self, ctx: commands.Context) -> None:
        """Regularly scan the server for users who have removed their avatar and
        kick them.
        """
        state = await self.config.guild(ctx.guild).kick_scheduler_on()
        if state:
            confirmed, _msg = await yesno("I'm scheduled to scan if users have removed their profile picture.", "Do you want to disable?")
            if confirmed:
                await self.config.guild(ctx.guild).kick_scheduler_on.set(False)

        if not state:
            confirmed, _msg = await yesno("This will configure me to regularly kick users who may remove their avatar.", "Do you want to enable?")
            if confirmed:
                await self.config.guild(ctx.guild).kick_scheduler_on.set(True)

    async def kick_check_errors(self, member: discord.Member) -> None:
        error_count = self.error_index[member.guild.id]

        if error_count > 10:
            return
        async with self.kick_sems[member.guild.id]:
            try:
                await member.kick()
            except discord.HTTPException:
                self.error_index += 1
                log.exception("Error kicking")

    @warden.command()
    @checks.has_permissions(administrator=True)
    async def purgeav(self, ctx: commands.Context):
        """Kick all users in the server that do not have an avatar set."""
        lock = self.purge_locks[ctx.guild.id]
        if lock.locked():
            return await ctx.send(embed=make_e("Alreadying running a kick for this server!", status=3))

        async with lock:
            guild: discord.Guild = ctx.guild
            to_kick = [self.kick_check_errors(m) for m in guild.members if not m.avatar and ctx.guild.me.top_role > m.top_role and m != guild.owner]
            if not to_kick:
                return await ctx.send(embed=make_e("Everyone in the server has an avatar set."))

            confirmed, _msg = await yesno(f"I'm going to kick {len(to_kick)} users from the server who do not have an avatar set.", "Please confirm")

            if not confirmed:
                return

            async with ctx.typing():
                self.error_index[ctx.guild.id] = 0
                self.active_tasks.append(asyncio.current_task())
                await asyncio.gather(*to_kick)

                return await ctx.send(embed=make_e("Finished!"))

    @warden.command(aliases=["welc", "joinmsg", "system"])
    @checks.has_permissions(administrator=True)
    async def welcome(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Auto-delete a system message and bot's welcome message when the member
        leaves suddenly.
        """
        if not channel:
            channel = ctx.channel

        settings = GuildSettings(**(await self.config.guild_from_id(ctx.guild.id).all(acquire_lock=False)))

        confirmation = BotConfirmation(ctx, 0x010101)
        if settings.delete_welcome_on_leave and self.bot.get_channel(settings.welcome_channel):
            await confirmation.confirm(
                "Deleting welcome messages is enabled.",
                description=f"Are you sure you want me to disable this? Currently monitoring {self.bot.get_channel(settings.welcome_channel).mention} ",
                hide_author=True,
                timeout=75,
            )

            if not confirmation.confirmed:
                return await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")

            await self.config.guild(ctx.guild).delete_welcome_on_leave.set(False)

            return await confirmation.update("Welcome message deleting disabled.", color=0x00F80C, hide_author=True)

        await confirmation.confirm(
            "System messages from Discord and bot welcome messages will be deleted when a user leaves.",
            description=f"Are you sure you want to enable this for channel {channel.mention}?",
            hide_author=True,
            timeout=75,
        )

        if not confirmation.confirmed:
            return await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")

        await self.config.guild(ctx.guild).delete_welcome_on_leave.set(True)
        await self.config.guild(ctx.guild).welcome_channel.set(channel.id)

        return await confirmation.update("Welcome message deleting enabled.", description="", color=0x00F80C, hide_author=True)

    @warden.command()
    @checks.has_permissions(administrator=True)
    async def joinavatar(self, ctx: commands.Context):
        """Kick members who join the server without a profile picture."""
        settings = GuildSettings(**(await self.config.guild_from_id(ctx.guild.id).all(acquire_lock=False)))

        confirmation = BotConfirmation(ctx, 0x010101)
        if settings.no_avatar_kick:
            await confirmation.confirm(
                "I'm kicking people who don't have an avatar set.",
                description="Are you sure you want me to disable this protection? ",
                hide_author=True,
                timeout=75,
            )

            if not confirmation.confirmed:
                return await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")

            await self.config.guild(ctx.guild).vanity_guard_enabled.set(False)

            return await confirmation.update("No avatar kicking disabled", color=0x00F80C, hide_author=True)

        await confirmation.confirm(
            "I'm going to kick anyone who joins the server without an avatar set.",
            description="Are you sure you want to enable this?",
            hide_author=True,
            timeout=75,
        )

        if not confirmation.confirmed:
            return await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")

        await self.config.guild(ctx.guild).no_avatar_kick.set(True)

        return await confirmation.update("No avatar kicking enabled", description="", color=0x00F80C, hide_author=True)

    @warden.command()
    @checks.has_permissions(administrator=True)
    async def rejoinlimit(self, ctx: commands.Context, max_joins: int, timespan: int) -> None:
        """Ban users that rejoin the server more than a certain number of times in
        a certain timespan.

        To disable, configure as `;warden rejoinlimit 0 0`

        Args:
        ----
            max_joins (number): The number of joins that the user can make in the timespan.
            timespan (number): In seconds, the timespan that the user can make the joins in.

        Example: `;warden rejoinlimit 3 60` will ban users that rejoin the server more than 3 times in the last 60 seconds.

        """
        await self.config.guild(ctx.guild).spam_join_max_joins.set(max_joins)
        await self.config.guild(ctx.guild).spam_join_timespam.set(timespan)
        await ctx.send(embed=make_e(f"Set the rejoin limit to {max_joins} joins in {timespan} seconds.", status=1))

    @checks.guildowner()
    @commands.command()
    async def hardban(self, ctx: commands.Context, user: discord.User):
        """Persistently ban a user from the server.

        Melanie will monitor all unbans and re-ban the user immediately.
        If the user is currently in the server they will be banned.

        """
        if ctx.author.id not in self.bot.owner_ids and ctx.author.id != ctx.guild.owner_id:
            if not ctx.author.guild_permissions.administrator:
                return await ctx.send(embed=make_e("You do not authorized to hardban", 3))
            if isinstance(user, discord.Member) and ctx.author.top_role <= user.top_role:
                return await ctx.send(embed=make_e("You do not authorized to hardban", 3))
        if isinstance(user, discord.Member) and ctx.guild.me.top_role <= user.top_role:
            return await ctx.send(embed=make_e("My top role is not high enough to ban them", 3))
        hardbans = await self.config.guild(ctx.guild).hardbans_set() or {}
        u_key = str(user.id)
        if u_key in hardbans:
            return await ctx.send(embed=make_e(f" {hardbans[u_key]['user_name']} is already hard banned", status=2))
        hardban_entry = {user.id: {"user_name": str(user), "user_id": user.id, "ban_author": {"name": str(ctx.author), "id": ctx.author.id}, "unbans": 0}}
        hardbans.update(hardban_entry)
        await ctx.guild.ban(
            user,
            reason=f"User cannot be unbanned without server owner approval. New hard ban requested for this user by {ctx.author} ({ctx.author.id})",
            delete_message_days=3,
        )
        await self.config.guild(ctx.guild).hardbans_set.set(hardbans)
        return await ctx.send(embed=make_e(f"Hard ban added for {user}"))

    @checks.guildowner()
    @commands.command()
    async def unhardban(self, ctx: commands.Context, user: discord.User):
        """Remove a user's hardban."""
        hardbans = await self.config.guild(ctx.guild).hardbans_set()

        u_key = str(user.id)
        if u_key not in hardbans:
            return await ctx.send(embed=make_e("That user is not currently hard banned.", status=2))

        del hardbans[u_key]

        await self.config.guild(ctx.guild).hardbans_set.set(hardbans)
        return await ctx.send(embed=make_e(f"Removed the hard ban for user id {user}. User is now able to be unbanned."))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if not guild.me.guild_permissions.ban_members:
            return
        hardbans: dict = await self.config.guild(guild).hardbans_set()
        if not hardbans:
            return
        u_key = str(user.id)
        if u_key not in hardbans:
            return
        author = hardbans[u_key]["ban_author"]
        reason = f"User cannot be unbanned without server owner approval. Hard ban requested for this user by {author['name']} ({author['id']})."
        await guild.ban(discord.Object(user.id), reason=reason)
        hardbans[u_key]["unbans"] += 1
        await self.config.guild(guild).hardbans_set.set(hardbans)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild: discord.Guild = member.guild

        settings = GuildSettings(**(await self.config.guild(guild).all(acquire_lock=False)))

        if not settings.delete_welcome_on_leave:
            return
        channel: discord.TextChannel = self.bot.get_channel(settings.welcome_channel)
        if not channel:
            return

        async with self.purge_locks[channel.id]:
            to_delete: list[discord.Message] = []
            name_fields = (member.name, member.mention, member.display_name)
            welcome_str = ("welc", "welcome", "hello")
            _warden_key = f"welcome:{member.id}:{channel.id}"

            _message_ident = await self.bot.redis.get(_warden_key)
            if _message_ident:
                _message_ident = int(_message_ident.decode())

            oldest_msg_date = arrow.utcnow().shift(days=-14).naive
            oldest_msg_date_ts = oldest_msg_date.timestamp()

            async for m in channel.history(limit=100):
                if m.created_at.timestamp() <= oldest_msg_date_ts:
                    break
                m: discord.Message
                if m.id == _message_ident:
                    to_delete.append(m)
                    continue
                if any(x in m.system_content for x in name_fields):
                    to_delete.append(m)
                elif m.author.bot and any(x in m.content for x in name_fields) and any(x in m.content for x in welcome_str):
                    to_delete.append(m)
                elif m.author.bot and m.embeds:
                    for embed in m.embeds:
                        try:
                            if any(x in embed.description for x in name_fields) and any(x in embed.description for x in welcome_str):
                                to_delete.append(m)
                                break
                        except (TypeError, ValueError):
                            continue
            if to_delete:
                m: discord.Message
                await channel.delete_messages(to_delete)
