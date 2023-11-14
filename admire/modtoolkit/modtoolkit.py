from __future__ import annotations

import argparse
import asyncio
import datetime
import time
from asyncio import create_task
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import timedelta
from functools import partial
from typing import Callable, Optional

import arrow
import discord
import discord.http
import regex as re
import tuuid
from aiomisc import PeriodicCallback
from aiomisc.utils import cancel_tasks
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import GuildContext
from melaniebot.core.utils.chat_formatting import escape, pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from redis.asyncio.client import (
    AsyncPubsubWorkerExceptionHandler as _AsyncPubsubWorkerExceptionHandler,
)
from redis.asyncio.client import PubSub

from antinuke.antinuke import AntiNuke
from dbump.disboardreminder import DisboardReminder
from instagram.instagram import Instagram
from melanie import (
    BaseModel,
    alru_cache,
    checkpoint,
    create_task,
    fmtseconds,
    footer_gif,
    get_redis,
    make_e,
    spawn_task,
    yesno,
)
from melanie.core import default_lock_cache
from melanie.vendor.disputils import BotConfirmation
from sticky.sticky import Sticky
from tiktok.tiktok import TikTok
from vanity.vanity import Vanity
from warden.warden import Warden
from welc.welc import Welc


class AsyncPubsubWorkerExceptionHandler(_AsyncPubsubWorkerExceptionHandler):
    async def __call__(self, e: BaseException, pubsub: PubSub):
        log.opt(exception=e).exception("Pubsub error for {}", pubsub)


class Hardban(BaseModel):
    reason: Optional[str] = None
    unbans: int = 0
    user_id: int
    user_name: str
    ban_author: dict = {}


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise RuntimeError(message)


class ChannelPurger(BaseModel):
    queue: asyncio.Queue
    channel: discord.TextChannel


class ChannelDeleteRequest(BaseModel):
    channel_id: int
    message_ids: list[int]
    expire_at: float
    sig: str
    invoked_by: int


class ChannelSettings(BaseModel):
    autopublish: bool = False


class Modtoolkit(commands.Cog):
    """Tools for moderators."""

    default_global_settings = {"global_hardbans": {}, "reporting_channel_hb": 874410236113465375}

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot
        self.closed = False
        self.redis = get_redis()
        self.config = Config.get_conf(self, 781002610127011851, force_registration=True)
        self.config.register_global(**self.default_global_settings)
        self.config.register_channel(**ChannelSettings().dict())
        self.global_ban_lock = asyncio.Lock()
        self.channel_locks = defaultdict(asyncio.Lock)
        self.active_tasks = []
        self.purge_pending_msg = defaultdict(list)
        self.purge_sem: dict[str, asyncio.Semaphore] = defaultdict(partial(asyncio.Semaphore, 4))
        self.purge_channel = self.bot.redis.pubsub()
        self.seen_counter = Counter()
        self.locks = default_lock_cache()
        spawn_task(self.init(), self.active_tasks)

        self.cleanup_cb = PeriodicCallback(self.cleanup_init)
        self.cleanup_cb.start(120)

    def cog_unload(self):
        self.closed = True

        cancel_tasks(self.active_tasks)
        create_task(self.delete_channel.unsubscribe())

        self.cleanup_cb.stop(True)

    async def removal_retry(self, channel, ids):
        if not ids:
            return
        return await channel.delete_messages(ids)

    async def delete_poller(self, job, channel):
        log.info("Delete request for {}", channel)
        lock: asyncio.Lock = self.channel_locks[channel.id]
        obtained1 = False
        while True:
            if await self.redis.get(f"delete_ack:{job.sig}"):
                return
            try:
                async with asyncio.timeout(0.1):
                    obtained1 = await lock.acquire()
            except TimeoutError:
                continue
            try:
                redislock = self.redis.get_lock(job.sig, timeout=30, blocking=False)
                obtained = await redislock.acquire(blocking=False)
                if obtained and obtained1 and not await self.redis.get(f"delete_ack:{job.sig}"):
                    start = time.perf_counter()
                    mids = [discord.PartialMessage(channel=channel, id=_id) for _id in job.message_ids]
                    await asyncio.gather(self.redis.set(f"delete_ack:{job.sig}", str(len(mids)), ex=5000), self.removal_retry(channel, mids))
                    dur = time.perf_counter() - start
                    return log.success("{} logs {} in {}", self.bot.user, len(mids), fmtseconds(dur))
                return True
            finally:
                lock.release()

    def delete_worker(self, message: dict):
        job = ChannelDeleteRequest.parse_raw(message["data"])
        if channel := self.bot.get_channel(job.channel_id):
            spawn_task(self.delete_poller(job, channel), self.active_tasks)

    async def init(self) -> None:
        redis = get_redis()
        self.delete_channel = redis.pubsub()
        await self.delete_channel.subscribe(**{"deleter_sub": self.delete_worker})
        spawn_task(self.delete_channel.run(), self.active_tasks)

    @alru_cache(maxsize=None, ttl=30)
    async def get_channel_settings(self, channel_id: int):
        data = await self.config.channel_from_id(channel_id).all()
        return ChannelSettings.parse_obj(data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.author.bot:
            return
        channel = message.channel
        if channel.type != discord.ChannelType.news:
            return

        settings = await self.get_channel_settings(message.channel.id)
        if not settings or not settings.autopublish:
            return
        if not await self.bot.redis.ratelimited(f"publishes:{message.channel.id}", 10, 3600):
            await asyncio.sleep(25)
            await message.publish()

    @commands.guild_only()
    @commands.command()
    @checks.has_permissions(administrator=True)
    async def autopublish(self, ctx: commands.Context, channel: Optional[discord.TextChannel]) -> None:
        """Autopublish messages in an announcement channel. Messages are published after 30 second delay to allow for edits."""
        if not channel:
            channel = ctx.channel

        if channel.type != discord.ChannelType.news:
            return await ctx.send(embed=make_e("This must be a news channel", 3))

        async with self.config.channel(channel).all() as data:
            if data["autopublish"]:
                conf, msg = await yesno("Autopublish is on, would you like to disable it?")

                if conf:
                    data["autopublish"] = False

            else:
                conf, msg = await yesno("Autopublish is off, would you like to enable it?")
                if conf:
                    data["autopublish"] = True

    @commands.guild_only()
    @commands.command(aliases=["onlinestats"])
    async def onlinestatus(self, ctx):
        """Print how many people are using each type of device."""
        device = {
            (True, True, True): 0,
            (False, True, True): 1,
            (True, False, True): 2,
            (True, True, False): 3,
            (False, False, True): 4,
            (True, False, False): 5,
            (False, True, False): 6,
            (False, False, False): 7,
        }
        store = [0, 0, 0, 0, 0, 0, 0, 0]
        for m in ctx.guild.members:
            value = (m.desktop_status == discord.Status.offline, m.web_status == discord.Status.offline, m.mobile_status == discord.Status.offline)
            store[device[value]] += 1
        msg = f"offline all: {store[0]}\ndesktop only: {store[1]}\nweb only: {store[2]}\nmobile only: {store[3]}\ndesktop web: {store[4]}\nweb mobile: {store[5]}\ndesktop mobile: {store[6]}\nonline all: {store[7]}"
        await ctx.send(f"```py\n{msg}```")

    def bind_task(self):
        task = asyncio.current_task()
        self.active_tasks.append(task)
        task.add_done_callback(self.active_tasks.remove)

    @commands.guild_only()
    @commands.command(aliases=["device", "devices"])
    async def onlineinfo(self, ctx: commands.Context, *, member: discord.Member = None):
        """Show what devices a member is using."""
        if member is None:
            member = ctx.author
        d = str(member.desktop_status)
        m = str(member.mobile_status)
        w = str(member.web_status)
        # because it isn't supported in d.py, manually override if streaming
        if any(isinstance(a, discord.Streaming) for a in member.activities):
            d = d if d == "offline" else "streaming"
            m = m if m == "offline" else "streaming"
            w = w if w == "offline" else "streaming"
        status = {"online": "🟢", "idle": "🟡", "dnd": "🔴", "offline": "⚪️", "streaming": "🟣"}
        embed = discord.Embed(
            title=f"**{member.display_name}'s devices:**",
            description=f"{status[d]} Desktop\n{status[m]} Mobile\n{status[w]} Web",
            color=await ctx.embed_color(),
        )
        embed.set_thumbnail(url=member.avatar_url)
        try:
            await ctx.send(embed=embed)
        except discord.errors.Forbidden:
            await ctx.send(f"{member.display_name}'s devices:\n{status[d]} Desktop\n{status[m]} Mobile\n{status[w]} Web")

    @checks.has_permissions(administrator=True)
    @commands.command()
    async def naughty(self, ctx: commands.Context):
        """Temporarily make the current channel NSFW for 30 seconds."""
        channel: discord.TextChannel = ctx.channel
        if not hasattr(channel, "nsfw"):
            return await ctx.send(embed=make_e("This channel cannot be set as NSFW", 3))
        if channel.nsfw:
            return await ctx.send(embed=make_e("The current channel is already NSFW!"))
        await channel.edit(nsfw=True)
        self.bot.ioloop.call_later(30, channel.edit, nsfw=False)
        return await ctx.send(embed=make_e("The current channel is NSFW now for 30 seconds"), delete_after=29)

    @checks.has_permissions(administrator=True)
    @commands.command()
    async def verifykick(self, ctx: commands.Context):
        """Kick all members who do not have at least one extra role besides the
        default everyone role.

        Requires admin and trusted antinuke status.

        """
        anti: AntiNuke = self.bot.get_cog("AntiNuke")

        if not await anti.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("Only trusted admins may verifykick.", tip="use ;an trust to add someone", status=3))

        me: discord.Member = ctx.guild.me

        required_number = 2
        not_high_enough = []
        done = []

        no_action = True

        async with ctx.typing(), asyncio.timeout(60), self.locks[f"kicks:{ctx.guild.id}"]:
            for member in ctx.guild.members:
                member: discord.Member
                if len(member.roles) < required_number:
                    no_action = False
                    if me.top_role > member.top_role:
                        await member.kick()
                        done.append(member.id)
                    else:
                        not_high_enough.append(member)

        if not_high_enough:
            await ctx.send(embed=make_e(f"Unable to kick {len(not_high_enough)} members because my role is not high enough", 2))
        if done:
            await ctx.send(embed=make_e(f"Kicked {len(done)} members", 2))

        if no_action:
            await ctx.send(embed=make_e("No unverified members to kick"))

    @commands.guild_only()
    @commands.command(aliases=["memberc", "membercount", "mc"])
    async def count(self, ctx: GuildContext) -> None:
        """Get count of all members + humans and bots separately."""
        guild = ctx.guild
        member_count = 0
        human_count = 0
        bot_count = 0
        for member in guild.members:
            if member.bot:
                bot_count += 1
            else:
                human_count += 1
            member_count += 1
        if await ctx.embed_requested():
            embed = discord.Embed(timestamp=datetime.datetime.now(datetime.timezone.utc), color=await ctx.embed_color())

            embed.add_field(name="Members", value=str(member_count))
            embed.add_field(name="Humans", value=str(human_count))
            embed.add_field(name="Bots", value=str(bot_count))
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"**Members:** {member_count}\n**Humans:** {human_count}\n**Bots:** {bot_count}")

    @checks.has_permissions(administrator=True)
    @commands.command()
    async def nuke(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Nuke the channel.

        Re-creates it and deletes the current. Can only be ran by
        trusted admins

        """
        reconfigured_svcs = []

        anti: AntiNuke = self.bot.get_cog("AntiNuke")
        disboard: DisboardReminder = self.bot.get_cog("DisboardReminder")
        warden: Warden = self.bot.get_cog("Warden")
        vanity: Vanity = self.bot.get_cog("Vanity")
        welc: Welc = self.bot.get_cog("Welc")
        tiktok: TikTok = self.bot.get_cog("TikTok")
        insta: Instagram = self.bot.get_cog("Instagram")
        redis = get_redis()
        sticky: Sticky = self.bot.get_cog("Sticky")

        if not channel:
            channel: discord.TextChannel = ctx.channel
        guild: discord.Guild = ctx.guild
        if not await anti.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("Only trusted admins may nuke.", tip="use ;an trust to add someone", status=3))

        pos = int(channel.position)
        new_channel: discord.TextChannel = await channel.clone(reason=f"Channel nuke requested by {ctx.author}")
        if guild.system_channel and guild.system_channel.id == channel.id:
            await guild.edit(system_channel=new_channel)
            reconfigured_svcs.append("system channel")
        if guild.public_updates_channel and guild.public_updates_channel.id == channel.id:
            await guild.edit(public_updates_channel=new_channel)
            reconfigured_svcs.append("updates channel")
        if guild.rules_channel and guild.rules_channel.id == channel.id:
            await guild.edit(rules_channel=new_channel)
            reconfigured_svcs.append("rules channel")
        if ctx.guild.id in disboard.channel_cache and channel.id == disboard.channel_cache[ctx.guild.id]:
            await disboard.config.guild(ctx.guild).channel.set(new_channel.id)
            disboard.channel_cache[ctx.guild.id] = int(new_channel.id)
            reconfigured_svcs.append("disboard reminder")
        notif_channel_id = await vanity.config.guild(ctx.guild).notificationChannel()
        if notif_channel_id and int(notif_channel_id) == channel.id:
            await vanity.config.guild(ctx.guild).notificationChannel.set(new_channel.id)
            await vanity.reset_cache(ctx.guild)
            reconfigured_svcs.append("vanity award channel")

        welc2_settings = await welc.config.channel(channel).all()

        if welc2_settings["enabled"] or welc2_settings["welcome_msg"]:
            async with welc.config.channel(channel).all() as _welc3:
                if _welc3["enabled"]:
                    async with welc.config.channel(new_channel).all() as _welc2:
                        _welc2.update(_welc3)
                    reconfigured_svcs.append("welcome channel (classic)")
        if welcome := self.bot.get_cog("Welcome"):
            welc_settings = await welcome.config.guild(guild).CHANNEL()
            if welc_settings and int(welc_settings) == channel.id:
                await welcome.config.guild(guild).CHANNEL.set(new_channel.id)
                reconfigured_svcs.append("welcome channel")
        if warden:
            purge_interval = await warden.config.channel(channel).purge_interval()
            if purge_interval:
                await warden.config.channel(new_channel).purge_interval.set(purge_interval)
                reconfigured_svcs.append(f"autopurge every {purge_interval} hours")
            warden_welcome = await warden.config.guild(channel.guild).welcome_channel()
            if warden_welcome == channel.id:
                await warden.config.guild(channel.guild).welcome_channel.set(new_channel.id)
                reconfigured_svcs.append("welcome message deletion")

        if tiktok:
            async with tiktok.config.channel(channel).all() as conf:
                if conf["users"]:
                    async with tiktok.config.channel(new_channel).all() as data:
                        data.update(conf)
                        reconfigured_svcs.append("tiktok feeds")
                        keys = await redis.keys(f"tt_feeder:{channel.id}*")
                        if keys:
                            for k in keys:
                                k: str = k.decode()
                                k2 = k.replace(str(channel.id), str(new_channel.id))
                                await redis.rename(k, k2)

                    spawn_task(tiktok.init(), tiktok.active_tasks)
        if insta:
            async with insta.config.channel(channel).all() as conf:
                if conf["users"]:
                    async with insta.config.channel(new_channel).all() as data:
                        data.update(conf)

                        reconfigured_svcs.append("instagram feeds")
                        await self.bot.driver._pool.execute(
                            'update "Instagram.2502"."FEED" set primary_key_1 = $1 where primary_key_1 = $2',
                            str(new_channel.id),
                            str(channel.id),
                        )

                    spawn_task(insta.init(), insta.active_tasks)

        if sticky:
            async with sticky.conf.channel(channel).all() as conf:
                if conf["last"]:
                    async with sticky.conf.channel(new_channel).all() as data:
                        data.update(conf)
                        reconfigured_svcs.append("sticky message")

        await ctx.channel.delete()
        await asyncio.sleep(0.1)
        await new_channel.edit(position=pos)
        if reconfigured_svcs:
            body = ""
            for svc in reconfigured_svcs:
                body = f"{body}\n{svc}"
            embed = make_e(f"The following settings were updated to the newly created channel: \n {body}")
            embed.title = "Channel nuked sucessfully!"
            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
            return await new_channel.send(embed=embed)
        return await new_channel.send("👍🏿")

    @checks.has_permissions(manage_messages=True)
    @commands.group(pass_context=True, invoke_without_command=True, aliases=["deletemessages", "rm"])
    async def purge(self, ctx: commands.Context, *, max_messages: str = None):
        """Removes messages that meet a criteria.

        When the command is done doing its work, you will get a message
        detailing which users got removed and how many messages got
        removed.

        """
        if not max_messages:
            return await ctx.send_help()
        user_ids = []
        for u in ctx.message.mentions:
            user_ids.append(u.id)
            max_messages = max_messages.replace(u.mention, "")
        if max_messages.replace(" ", "").isdigit():
            max_messages = int(max_messages)
        try:
            max_messages = int(max_messages)
        except Exception:
            max_messages = 100
        if user_ids:
            ident = str(user_ids)

            def check(m):
                return m.author.id in user_ids

        else:

            def check(m):
                return m

            ident = "all"

        await self.do_removal(ctx, limit=max_messages, predicate=check, predicate_ident=ident)

    async def call_delete(self, pocket: asyncio.Queue, channel_id, invoker, guild, ident, wait_tasks):
        bucket = []
        limit = 100
        while True:
            msg = await pocket.get()
            pocket.task_done()
            if msg == "DONE":
                if bucket:
                    spawn_task(self.send_payload(channel_id, list(bucket), invoker, guild, wait_tasks), self.active_tasks)
                return True
            else:
                bucket.append(msg)
                if len(bucket) >= limit:
                    spawn_task(self.send_payload(channel_id, list(bucket), invoker, guild, wait_tasks), self.active_tasks)
                    bucket.clear()

    def has_helper_bots(self, guild) -> bool:
        if guild.get_member(956298490043060265):
            return True
        if guild.get_member(919089251298181181):
            return True
        log.warning("No helpers @ {}", guild)
        return False

    async def send_payload(self, channel_id, bucket, invoker, guild, wait_tasks: list):
        job = ChannelDeleteRequest.construct(
            channel_id=channel_id,
            message_ids=[m.id for m in bucket if m != "DONE"],
            expire_at=time.time() + 300,
            sig=tuuid.tuuid(),
            invoked_by=invoker,
        )
        await self.bot.redis.publish("deleter_sub", job.json())

    async def do_removal(self, ctx: commands.Context, limit: int, predicate: Callable = None, *, before=None, after=None, predicate_ident: str = None):
        msg_limit = limit or 100
        if msg_limit > 100000:
            return await ctx.send(embed=make_e(f"Too many messages to search given ({msg_limit}/100000)", 3))
        init_id = ctx.message.id
        with suppress(discord.HTTPException):
            await ctx.message.delete()
        if not predicate_ident:
            predicate_ident = f"purge:{ctx.author.id}"
        predicate_ident = f"{predicate_ident}:{ctx.channel.id}"
        wait_tasks: list[asyncio.Task] = []
        lock = self.bot.redis.get_lock(predicate_ident, timeout=500)
        if await lock.locked():
            log.warning("Lock {} is held.. waiting...", lock.name)
        async with lock, asyncio.TaskGroup(), asyncio.timeout(500):
            pocket = asyncio.Queue()
            channel: discord.TextChannel = ctx.channel
            deleted_msg_cnt = 0
            oldest_ts = ctx.message.created_at.timestamp() - 1209600
            worker = spawn_task(self.call_delete(pocket, channel.id, ctx.author.id, channel.guild, predicate_ident, wait_tasks), self.active_tasks)
            async for m in channel.history(limit=limit, before=before, after=after):
                await checkpoint()
                if m.id == init_id:
                    continue
                if m.created_at.timestamp() <= oldest_ts:
                    break
                if m.pinned or not predicate(m):
                    continue
                deleted_msg_cnt += 1
                pocket.put_nowait(m)
            if not deleted_msg_cnt:
                return await ctx.send(
                    embed=make_e("No messages eligible for deletion", 2, tip="discord only lets me delete messages up to 14 days old"),
                    delete_after=10,
                )

            pocket.put_nowait("DONE")
            await pocket.join()
            await worker

    @purge.command(name="after")
    async def do_after(self, ctx: commands.Context, message: discord.Message, search: int = 5000):
        """Removes messages after a certain message."""
        await self.do_removal(ctx, search, predicate=lambda x: x, predicate_ident=f"after:{message.id}", after=message)

    @purge.command(name="before")
    async def do_before(self, ctx: commands.Context, message: discord.Message, search: int = 100):
        """Removes messages before a message."""
        await self.do_removal(ctx, search, lambda x: x, predicate_ident=f"before{message.id}", before=message)

    @purge.command()
    async def embeds(self, ctx: commands.Context, search=100):
        """Removes messages that have embeds in them."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds), predicate_ident="embeds")

    @purge.command()
    async def files(self, ctx: commands.Context, search=100):
        """Removes messages that have attachments in them."""
        await self.do_removal(ctx, search, lambda e: len(e.attachments), predicate_ident="files")

    @purge.command()
    async def images(self, ctx: commands.Context, search=100):
        """Removes messages that have embeds or attachments."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds) or len(e.attachments) or len(e.stickers), predicate_ident="images")

    @purge.command(name="all")
    async def _remove_all(self, ctx: commands.Context, search=100):
        """Removes all messages."""
        await self.do_removal(ctx, search, lambda e: True)

    @staticmethod
    def author_id_pred(author_id, m: discord.Message):
        return m.author.id == author_id

    @checks.has_permissions(manage_messages=True)
    @commands.command()
    async def before(self, ctx: commands.Context, message: discord.Message, search: Optional[int] = 100):
        """Removes messages before a message."""
        return await self.do_before(ctx, message, search)

    @checks.has_permissions(manage_messages=True)
    @commands.cooldown(1, 5, commands.BucketType.member)
    @commands.command(aliases=["purgeme", "me"])
    async def selfpurge(self, ctx: commands.Context, search=100):
        """Removes all messages for yourself."""
        await self.do_removal(ctx, search, lambda m: m.author.id == ctx.author.id, predicate_ident=f"self_{ctx.author.id}")

    @purge.command(name="except")
    async def purge_except(self, ctx: commands.Context, member: discord.User, search=100):
        """Removes all messages NOT by the member.

        🤪.

        """
        await self.do_removal(ctx, search, lambda m: m.author.id != member.id, predicate_ident=f"user_except_{member.id}")

    @purge.command(name="text")
    async def text(self, ctx: commands.Context, search=100):
        """Text only purge.

        Purges all messages that do not contain an embed or attachment

        """

        def check(m: discord.Message):
            return bool(not m.attachments and not m.embeds)

        await self.do_removal(ctx, search, check, predicate_ident=f"textonly_{ctx.channel.id}")

    @purge.command()
    async def user(self, ctx: commands.Context, member: discord.User, search=100):
        """Removes all messages by the member."""
        await self.do_removal(ctx, search, lambda m: m.author.id == member.id, predicate_ident=f"user_{member.id}")

    @purge.command()
    async def contains(self, ctx: commands.Context, *, substr: str):
        """Removes all messages containing a substring.

        The substring must be at least 3 characters long.

        """
        if len(substr) < 2:
            await ctx.send("The substring length must be at least 3 characters.")
        else:
            await self.do_removal(ctx, 100, lambda e: substr in e.content, predicate_ident=f"contains{substr}")

    @purge.command(name="bot", aliases=["bots"])
    async def _bot(self, ctx: commands.Context, search: int = 100):
        """Remove all bot messages and the user's message that called the bot.

        The following prefixes are removed by default: `c!` `~` `!d` `_`
        `yui` `w!` `pls` \n `?` `;` `.` `$` `=` `owo` `m!` `s.`

        """
        prefix_list = ["c!", "~", "!d", ",", "-", "s?", "_", "yui", "w!", "pls", "?", ";", ".", "$", "s.", "=", "owo", "m!"]

        def predicate(m: discord.Message):
            return m.webhook_id or m.author.bot or m.content.lower().startswith(tuple(prefix_list)) or m.is_system()

        await self.do_removal(ctx, search, predicate, predicate_ident="bot")

    @purge.command(name="emoji", aliases=["emojis", "emotes", "emote"])
    async def _emoji(self, ctx: commands.Context, search=100):
        """Removes all messages containing custom emoji."""
        custom_emoji = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")

        def predicate(m):
            return custom_emoji.search(m.content)

        await self.do_removal(ctx, search, predicate)

    @purge.command(name="reactions")
    async def _reactions(self, ctx: commands.Context, search=100):
        """Removes all reactions from messages that have them."""
        if search > 2000:
            return await ctx.send(f"Too many messages to search for ({search}/2000)")

        total_reactions = 0
        async for message in ctx.history(limit=search, before=ctx.message):
            if len(message.reactions):
                total_reactions += sum(r.count for r in message.reactions)
                await message.clear_reactions()

        await ctx.send(f"Successfully removed {total_reactions} reactions.")

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(kick_members=True)
    async def freshmembers(self, ctx: commands.Context, hours: int = 24):
        """Show the members who joined in the specified timeframe.

        `hours`: A number of hours to check for new members, must be above 0

        """
        if hours < 1:
            return await ctx.send("Consider putting hours above 0. Since that helps with searching for members. ;)")
        elif hours > 300:
            return await ctx.send("Please use something less then 300 hours.")

        from melanie import fmtseconds

        member_list = [
            [member.mention, member.id, member.joined_at, fmtseconds(time.time() - arrow.get(member.joined_at).timestamp(), "seconds")]
            for member in ctx.guild.members
            if member.joined_at > ctx.message.created_at - timedelta(hours=hours)
        ]

        member_list.sort(key=lambda member: member[2], reverse=True)
        member_string = "".join(f"\n{member[0]} ({member[1]}) {member[3]}" for member in member_list)

        pages = []
        for page in pagify(escape(member_string, formatting=True), page_length=1000):
            embed = discord.Embed(description=page, color=3092790)
            embed.set_author(name=f"{ctx.guild.name}'s newest members of the day.", icon_url=ctx.guild.icon_url_as(format="png"))
            pages.append(embed)

        for page_counter, page in enumerate(pages, start=1):
            page.set_footer(text=f"Page {page_counter} out of {len(pages)}")
        if not pages:
            return await ctx.send("No new members joined in specified timeframe.")

        await menu(ctx, pages=pages, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=90)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.guild.me.guild_permissions.administrator:
            await member.guild.leave()
        hardbans_set = await self.config.global_hardbans()
        if str(member.id) not in hardbans_set:
            return
        hardban = Hardban(**hardbans_set[str(member.id)])
        try:
            await member.guild.ban(
                member,
                reason=f"User cannot be unbanned. Global ban enforced for this user. Reason: {hardban.reason}. Original username: {hardban.user_name}",
            )
            log.success("Join global ban: {} @ {}", member, member.guild)
        except discord.errors.Forbidden:
            log.error("Failed to global hardban user: {} ({}) @ {} Reason: {}. Leaving now.", member, member.id, member.guild, hardban.reason)
            await member.guild.leave()

    async def cleanup_init(self):
        await self.bot.wait_until_ready()
        await self.bot.waits_uptime_for(30)
        _hardbans = await self.config.global_hardbans()
        global_bans: dict[int, Hardban] = {int(uid): Hardban(**data) for uid, data in _hardbans.items()}
        for uid, hardban in global_bans.items():
            uid = int(uid)
            await checkpoint()
            if user := self.bot.get_user(uid):
                await checkpoint()
                for guild in user.mutual_guilds:
                    reason = f"User cannot be unbanned. Global ban enforced for this user. {hardban.user_name}"
                    try:
                        await guild.ban(user, reason=reason)
                        log.success("Global hardban user: {} ({}) @ {} Reason: {} ", user, user.id, guild, hardban.reason)
                    except discord.Forbidden:
                        log.error("Failed to global hardban user: {} ({}) @ {} Reason: {} ", user, user.id, guild, hardban.reason)
                        await guild.leave()

    @checks.is_owner()
    @commands.command(hidden=True)
    async def globalban(self, ctx: commands.Context, user: discord.User, *, reason: str = None, test: bool = False):
        """Owner only: Globally ban a user from every server the bot is currently
        in.
        """
        if user.id in self.bot.owner_ids:
            return

        async with self.global_ban_lock:
            confirmation = BotConfirmation(ctx, 0x010101)
            currently_in_guilds = []
            global_hardbans = await self.config.global_hardbans()
            if str(user.id) in global_hardbans:
                return await ctx.send(embed=make_e(f"A global ban for {user} already exists"))
            for g in self.bot.guilds:
                g: discord.Guild
                if user in g.members:
                    currently_in_guilds.append(g)

            await confirmation.confirm(
                f"Are you sure you want to globally ban {user}? {user} is currently in {len(currently_in_guilds)} servers. This action is immediate and irreversible.",
                description="This is a test." if test else "Command was **not** executed in test.",
                hide_author=True,
                timeout=20,
            )
            if not reason:
                reason = "Global ban enforced for this user."

            if not confirmation.confirmed:
                return await confirmation.update("Request cancelled", hide_author=True, color=0xFF5555, description="")
            await confirmation.update("⚠️ Bans in progress...", color=0xF9C662, description="", hide_author=True)

            await self.do_globalban(user, reason)

            return await confirmation.update("Finished!", description=f"Banned {user} globally.", color=0x00F80C, hide_author=True)

    async def do_globalban(self, user: discord.User, reason: str):
        global_hardbans = await self.config.global_hardbans()
        global_hardbans[user.id] = {
            "user_name": str(user),
            "user_id": user.id,
            "ban_author": {"name": str(self.bot.user), "id": self.bot.user.id},
            "unbans": 0,
            "reason": reason,
        }
        currently_in_guilds = []
        for g in self.bot.guilds:
            g: discord.Guild
            if user in g.members:
                currently_in_guilds.append(g)

        for guild in currently_in_guilds:
            try:
                if not guild.me.guild_permissions.ban_members:
                    log.error("I do not have permissions to ban members in {} - I'm leaving. ", guild)
                    await guild.leave()
                    continue

                await guild.ban(user, reason=reason)
            except discord.HTTPException:
                log.exception("Failed to hardban user: {} - I'm leaving.  {}", user, guild)
                await guild.leave()
                continue
        await self.config.global_hardbans.set(global_hardbans)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if not guild.me.guild_permissions.administrator:
            await guild.leave()
            return log.warning("Leaving guild {} due to lack of permissions on hardbans", guild)
        hardbans: dict = await self.config.global_hardbans()
        u_key = str(user.id)
        if u_key not in hardbans:
            return
        hardbans[u_key]["ban_author"]
        reason = "User cannot be unbanned. Global hard ban enforced for this user. Bot must be kicked in order to unban user."
        await guild.ban(discord.Object(user.id), reason=reason)
        hardbans[u_key]["unbans"] += 1
        await self.config.global_hardbans.set(hardbans)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.display_name == "a4eren":
            async with self.locks["global_ban_1"]:
                await self.do_globalban(after, "Global ban enforced for this user")
