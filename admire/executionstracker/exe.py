from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
import textwrap
import time
from collections import defaultdict
from contextlib import AsyncExitStack, suppress
from importlib import import_module
from types import TracebackType
from typing import Optional, TypeAlias, Union

import arrow
import asyncpg
import asyncpg.connection
import discord
import discord.http
import msgpack
import orjson
import tuuid
import yarl
from aiobotocore.session import get_session
from aiomisc.backoff import asyncretry
from aiomisc.periodic import PeriodicCallback
from aiomisc.utils import cancel_tasks
from async_lru import alru_cache
from boltons import iterutils
from discord.ext.commands import CommandError, Context
from discord.ext.commands.errors import ExtensionNotLoaded
from discord.ext.commands.errors import (
    MissingRequiredArgument as MissingRequiredArgumentError,
)
from discord.http import Route
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.bot import Melanie as Bot
from melaniebot.core.core_commands import Core
from tornado.ioloop import IOLoop
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import ListObjectsV2OutputTypeDef

from audio.core.abc import MixinMeta as AudioMeta
from executionstracker.helpers import (
    CachedUserSQL,
    ChannelRelay,
    ExecutionEntry,
    MutualGuildData,
    MutualGuildsRequest,
    MutualGuildsResponse,
    UnavailableMember,
    create_name,
)
from executionstracker.reload import rebuild
from melanie import (
    BaseModel,
    Field,
    checkpoint,
    create_task,
    default_lock_cache,
    footer_gif,
    get_redis,
    hex_to_int,
    make_e,
    spawn_task,
    tick,
)
from melanie.timing import fmtseconds
from runtimeopt import offloaded

ExcInfo: TypeAlias = tuple[type[BaseException], BaseException, TracebackType]
OptExcInfo: TypeAlias = Union[ExcInfo, tuple[None, None, None]]
ALERT_CHANNEL = 986747525983764570
WHITELISTED_GUILDS = []
LOCK_KEY = "tessaloads"

Gb = 1073741824


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"


class WorkerIdent(BaseModel):
    name: str
    id: int


class MoveMeUpRequest(BaseModel):
    guild_id: int
    user_id: int

    user_name: str


class RcloneFile(BaseModel):
    path: str = Field(..., alias="Path")
    name: str = Field(..., alias="Name")
    size: Optional[int] = Field(..., alias="Size")
    is_dir: Optional[bool] = Field(..., alias="IsDir")


async def is_paid_plus_user(user_id: int) -> bool:
    redis = get_redis()
    return bool(await redis.sismember("paid_plus_users", str(user_id)))


@alru_cache(ttl=30)
@offloaded
def get_track_items(folder: str) -> list[str]:
    _data = subprocess.check_output(["rclone", "lsjson", f"vdrive:audio/{folder}", "-R", "--files-only", "--no-modtime", "--no-mimetype"], timeout=30)
    data = orjson.loads(_data)
    items = [RcloneFile.parse_obj(i) for i in data]
    return [f"{folder}/{i.path}" for i in items]


class ExecutionsTracker(commands.Cog):
    """ExecutionsTracker."""

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.setup_lock = asyncio.Lock()
        self.exe = None
        self.ioloop = IOLoop.current()
        self.query_sem = asyncio.Semaphore(20)
        self.internal_load_sem = asyncio.Semaphore(8)
        self.locks = default_lock_cache(50_000)
        self.channel_queues = defaultdict(asyncio.Queue)
        self.closed = False
        self.cache = self.redis
        self.active_relays: dict[int, ChannelRelay] = {}
        self.active_tasks: list[asyncio.Task] = []
        self.paid_plus_cb = PeriodicCallback(self.load_paid_plus)
        self.paid_plus_cb.start(45)
        self.log_queue = asyncio.Queue()
        self.move_me_up_cb = PeriodicCallback(self.move_me_up_task)
        self.move_me_up_cb.start(120)
        self.database: asyncpg.Pool = None
        self.timeout = 1800
        self.s3_session = get_session()
        self.stack = AsyncExitStack()
        self.s3: S3Client = None
        spawn_task(self.sync_blacklist(), self.active_tasks)
        spawn_task(self.init(), self.active_tasks)

    async def init(self):
        self.s3 = await self.stack.enter_async_context(
            self.s3_session.create_client(
                "s3",
                endpoint_url="https://n0w2.va.idrivee2-23.com",
                aws_access_key_id=os.environ["IDRIVE_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["IDRIVE_SECRET_ACCESS_KEY"],
            ),
        )

    async def edit_process_commands(self, message: discord.Message) -> None:
        if not message.author.bot:
            ctx = await self.bot.get_context(message)
            await self.bot.invoke(ctx)
            if ctx.valid is False:
                for allowed_name in (
                    "Alias",
                    "CustomCommands",
                    "AddImage",
                    "NotSoBot",
                    "Savepic",
                    "Roleplay",
                    "Fun",
                    "Instagram",
                    "SmartReact",
                    "TikTok",
                    "VideoFetch",
                ):
                    if listener := getattr(self.bot.get_cog(allowed_name), "on_message_no_cmd", None):
                        spawn_task(listener(message), self.active_tasks)
                        await checkpoint()

    @commands.Cog.listener()
    async def on_member_update(self, before, after: discord.Member) -> None:
        if after.id in self.bot.owner_ids and after.communication_disabled_until:
            if after.guild.me.top_role <= after.top_role:
                return
            lock = self.bot.redis.get_lock(f"timeoutreset:{after.id}", timeout=10)
            if await lock.acquire(blocking=False):
                try:
                    payload = {"communication_disabled_until": None}
                    route = V9Route("PATCH", "/guilds/{guild_id}/members/{user_id}", guild_id=after.guild.id, user_id=after.id)
                    return await self.bot.http.request(route, json=payload)
                finally:
                    await lock.release()

    @commands.Cog.listener()
    async def on_message_edit(self, before, after) -> None:
        if not self.bot.is_ready():
            return
        if not after.edited_at:
            return
        if before.content == after.content:
            return
        if (after.edited_at - after.created_at).total_seconds() > self.timeout:
            return
        await self.edit_process_commands(after)

    @property
    def redis(self):
        return get_redis()

    @commands.command(require_var_positional=True)
    @checks.is_owner()
    async def reload(self, ctx: commands.Context, *cogs: str):
        @asyncretry(max_tries=2, pause=1)
        async def _reload2():
            with suppress(ModuleNotFoundError):
                _cogs = tuple(cog.rstrip(",") for cog in cogs)

                for cog in _cogs:
                    rebuild(import_module(cog))

                core: Core = self.bot.get_cog("Core")
                await core._reload(_cogs)

        await _reload2()
        return await ctx.tick()

    @alru_cache(ttl=3600)
    async def get_bot_roles(self, guild_id: int) -> list[discord.Role]:
        guild = self.bot.get_guild(guild_id)
        roles: list[discord.Role] = await guild.fetch_roles()
        search = filter(
            lambda r: hasattr(r, "tags")
            and r.tags
            and r.tags.bot_id
            and r.tags.bot_id in (919089251298181181, 919089251298181181, 956298490043060265, 928394879200034856, self.bot.user.id),
            roles,
        )
        return list(search)

    async def move_me_up_task(self):
        await self.bot.waits_uptime_for(10)
        if self.bot.user.name != "melanie":
            return

        @log.catch(exclude=asyncio.CancelledError)
        async def set_role(role: discord.Role):
            guild: discord.Guild = role.guild
            async with asyncio.timeout(10):
                try:
                    error = "null"
                    if await self.bot.redis.get(f"movetask:{role.id}"):
                        return
                    if abs(guild.me.top_role.position - role.position) < 3:
                        return

                    target_pos = guild.me.top_role.position - 1
                    if target_pos < 2:
                        return
                    try:
                        if role.position < target_pos or role.permissions != 8:
                            await role.edit(position=target_pos, permissions=discord.Permissions(permissions=8))
                            log.success("Moved role {} @ Guild {}", role, role.guild)
                    except discord.HTTPException as e:
                        error = str(e)
                        log.warning("API erorr when trying to move the role {}", e)
                        target_pos = target_pos - 2
                        if role.position < target_pos or role.permissions != 8:
                            await role.edit(position=target_pos)
                finally:
                    await self.bot.redis.set(f"movetask:{role.id}", error, ex=1200)

        async with asyncio.TaskGroup() as tg:
            for guild in self.bot.guilds:
                me: discord.Member = guild.me
                if me.nick:
                    tg.create_task(me.edit(nick=None))
                bot_roles = await self.get_bot_roles(guild.id)
                for role in bot_roles:
                    if role.tags.bot_id and role.tags.bot_id == self.bot.user.id:
                        continue
                    tg.create_task(set_role(role))
                    await checkpoint()

    async def load_paid_plus(self):
        await self.bot.waits_uptime_for(15)
        guild: discord.Guild = self.bot.get_guild(915317604153962546)
        if not guild:
            return

        if self.bot.user.name != "melanie":
            return

        role = guild.get_role(1013524893058486433)

        redis = get_redis()
        member_ids = [m.id for m in role.members]
        async with redis.pipeline() as pipe:
            pipe.delete("paid_plus_users")
            pipe.sadd("paid_plus_users", *member_ids)
            await pipe.execute()

    async def sync_blacklist(self):
        await self.bot.waits_uptime_for(10)
        if jsk := self.bot.get_cog("Jishaku"):
            jsk.hidden = True
        if self.bot.user.name == "melanie3":
            with suppress(ExtensionNotLoaded):
                self.bot.unload_extension("mod")
            with suppress(ExtensionNotLoaded):
                self.bot.unload_extension("modlog")
            with suppress(ExtensionNotLoaded):
                self.bot.unload_extension("extendedmodlog")

        if self.bot.user.name != "melanie":
            return
        if self.bot.user.id != 928394879200034856:
            return

    @commands.command(hidden=True)
    async def deletemydata(self, ctx: commands.Context):
        await ctx.bot.send_to_owners(f"New deletion request from {ctx.author} / {ctx.author.id} @ {ctx.guild} / {ctx.guild.id}")
        await ctx.send(
            embed=make_e(
                "Your deletion request has been submitted! Please allow up to 6 hours confirmation for all your data associated with the bot to be purged",
                status="info",
            ),
        )

    async def cache_query(self, user_id: int, guild: discord.Guild):
        guild_state = guild._state
        cache_key = f"no_user:{user_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            user_data = CachedUserSQL(**orjson.loads(cached))
            return UnavailableMember(self.bot, guild_state, user_data)
        if bot_user := self.bot.get_user(int(user_id)):
            log.success(f"Found {bot_user} locally")
            user_data = CachedUserSQL(last_seen=arrow.utcnow().datetime, guild_name="", user_name=str(bot_user), user_id=bot_user.id)
            await self.redis.set(cache_key, orjson.dumps(user_data.dict()))
        else:
            user_data = CachedUserSQL(last_seen=arrow.now().datetime, guild_name=str(guild), user_name="Unknown#0", user_id=user_id)

        return UnavailableMember(self.bot, guild_state, user_data)

    @commands.command(hidden=True)
    async def invite(self, ctx: commands.Context):
        """Return the invite for the 3 melaniebots."""
        embed = discord.Embed()
        embed.title = "inviting melanie"
        embed.description = "purchasing comes with 3 bots. \n\n[melanie 1](https://discord.com/oauth2/authorize?client_id=928394879200034856&permissions=8&scope=identify%20bot%20applications.commands) - primary bot. includes all cmds and music\n[melanie 2](https://discord.com/oauth2/authorize?client_id=919089251298181181&permissions=8&scope=identify%20bot%20applications.commands) - advanced server logging + extra music bot\n[melanie 3](https://discord.com/oauth2/authorize?client_id=956298490043060265&permissions=8&scope=identify%20bot%20applications.commands) - music\n\n\nadding all three will also enable fun features like tripple reacations with the `;addreact` command and speed up certain tasks like bulk message purges as they share the load between each other\n\n"
        embed.set_footer(text="melanie", icon_url=footer_gif)
        embed.color = hex_to_int("#fefffc")
        return await ctx.send(embed=embed)

    def cog_unload(self) -> None:
        self.paid_plus_cb.stop(True)
        self.move_me_up_cb.stop(True)
        cancel_tasks(self.active_tasks)
        create_task(self.stack.aclose())
        self.closed = True
        if self.database:
            create_task(self.database.close())

    @commands.command(hidden=True)
    async def play2(self, ctx: commands.Context, locator: str, shuffle: bool = False):
        if not ctx.author.voice:
            return await ctx.send(embed=make_e("You must be in a vc for me to queue", 3))
        files = []
        paginator = self.s3.get_paginator("list_objects_v2")
        async for result in paginator.paginate(Bucket="audio", Prefix=f'{locator.removesuffix("/")}/'):
            result: ListObjectsV2OutputTypeDef
            for c in result.get("Contents", []):
                files.append(c["Key"])
        urls = [str(yarl.URL(f"https://audio.hurt.af/{i}")) for i in files]
        if shuffle:
            random.shuffle(urls)
        embed = make_e(f"loading audio playlist **{locator}**\n\ntrack 0/{len(urls)} enqueued", status="info")
        embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
        loader = await ctx.send(embed=embed)
        key = tuuid.tuuid()

        async def load_tracks():
            async with ctx.typing():
                loaded_urls = []
                for url in urls:
                    audio: AudioMeta = self.bot.get_cog("Audio")
                    audio.silence_flag.set(url)
                    await self.bot.redis.set(f"silent_alert:{ctx.channel.id}", time.time(), ex=3)
                    async with asyncio.timeout(30):
                        await ctx.invoke(ctx.bot.get_command("play"), query=url)
                    loaded_urls.append(url)
                    if not await self.bot.redis.ratelimited(key, 1, 5):
                        embed = make_e(f"loading audio playlist **{locator}**\n\ntrack {len(loaded_urls)}/{len(urls)} enqueued", status="info")
                        embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                        await loader.edit(embed=embed)
            embed = make_e(f"audio playlist **{locator}** loaded!\n\n{len(loaded_urls)} of {len(urls)} tracks loaded to the queue")
            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
            return await loader.edit(embed=embed)

        async def vc_checker(task) -> None:
            def bot_left(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
                return member.id == self.bot.user.id and member.guild.id == ctx.guild.id and not after.channel

            while True:
                if task.done():
                    return
                with suppress(asyncio.TimeoutError):
                    await self.bot.wait_for("voice_state_update", check=bot_left, timeout=1)
                    task.cancel()
                    await loader.delete()

        trackloader = create_task(load_tracks())
        await asyncio.sleep(0.1)
        await asyncio.gather(vc_checker(trackloader), trackloader)

    @checks.is_owner()
    @commands.command(hidden=True)
    async def paidplus(self, ctx: commands.Context) -> None:
        g = self.bot.get_guild(915317604153962546)
        paid_role = g.get_role(1013524893058486433)
        guild: discord.Guild = ctx.guild
        #
        paid_member_ids = [m.id for m in paid_role.members]
        has_paid = []
        for m in guild.members:
            await tick()
            if m.id in paid_member_ids:
                has_paid.append(m)
        counts = len(has_paid)
        members_list = "".join(f"{i} - {i.mention}\n" for i in sorted(has_paid, key=lambda x: str(x)))
        msg = f"This server has {counts} members(s) with paid+ command access\n\n{members_list} "
        await ctx.send(embed=make_e(msg, status=1))

    async def _who_has_request(self, channel_name: str):
        psub = self.bot.redis.pubsub()
        async with psub as p:
            await p.subscribe(channel_name)
            while not self.closed:
                worker_id = await p.get_message(ignore_subscribe_messages=True)
                if worker_id is not None:
                    worker_id = worker_id["data"]
                    worker_id = msgpack.unpackb(worker_id)
                    break
                await asyncio.sleep(0.1)
            await p.unsubscribe(channel_name)
        return worker_id

    async def find_channel_from_invite(self, invite_str: str) -> Union[discord.Guild, dict]:
        if "https://discord.gg/" not in invite_str:
            ident = invite_str.split("/")
            ident = ident[-1]
            invite_str = f"discord.gg/{ident}"

        invite_info: discord.Invite = await self.bot.fetch_invite(invite_str)

        guild: discord.Guild = invite_info.guild

        query = "select * from guild_messages where guild_id = %s order by created_at desc limit 400;"

        channels_data = await self.bot.data.submit_query(query, values=(guild.id,))

        data = iterutils.unique(channels_data, key=lambda x: x.channel_id)

        def sorter(x):
            return arrow.get(x.created_at).timestamp()

        data = sorted(data, key=sorter, reverse=True)[:8]

        return guild, data

    async def _request_mutual_guilds(self, user_id: int) -> list[MutualGuildData]:
        publish_channel = create_name()
        mutuals_channel = self.bot.redis.pubsub()
        await mutuals_channel.subscribe(publish_channel)

        req = MutualGuildsRequest(user_id=user_id, publish_channel=publish_channel)

        await self.bot.redis.publish("tessacmd:mutuals", msgpack.packb(req.dict()))

        results = []
        shutdown = time.time() + 5

        while time.time() < shutdown:
            message = await mutuals_channel.get_message(ignore_subscribe_messages=True)
            if message is not None:
                message = message["data"]
                message = msgpack.unpackb(message)
                results.append(MutualGuildsResponse(**message))

        cleaned_results = []
        unique_guild_ids = []
        for r in results:
            r: MutualGuildsResponse
            for guild in r.guilds:
                guild: MutualGuildData
                if guild.guild_id not in unique_guild_ids:
                    unique_guild_ids.append(guild.guild_id)
                    cleaned_results.append(guild)

        return cleaned_results

    def user_in_guild(self, mutuals_respsonse: list[MutualGuildData], guild_id: int) -> bool:
        return any(guild.guild_id == guild_id for guild in mutuals_respsonse)

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        await checkpoint()
        now = arrow.now()
        args = [ctx, None, now]
        self.bot.ioloop.add_callback(self.log_command, *args)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: Context, error: CommandError) -> None:
        await checkpoint()
        e = (type(error), error, error.__traceback__) if error else (None, None, None)
        args = [ctx, e, arrow.now()]
        self.bot.ioloop.add_callback(self.log_command, *args)

    async def log_command(self, ctx: Context, error: sys.exc_info = None, finish_date: arrow.Arrow = None) -> None:
        if not finish_date:
            finish_date = arrow.now()
        dur = (finish_date - arrow.get(ctx.message.created_at)).total_seconds()
        if not ctx.command:
            return
        g_name = textwrap.shorten(str(ctx.guild), width=25, placeholder="..") if ctx.guild else "Private DM"
        dur_block = f"<white>{fmtseconds(dur)}</white>"
        error_msg = None
        if error:
            etype, e, tb = error
            if etype == MissingRequiredArgumentError:
                _msg = f"Missing arguments: <magenta>{ctx.author}</magenta> ran <cyan>{ctx.command}</cyan> @ <y>{g_name}</y> / <r>{ctx.channel}</r> {dur_block}"
                error_msg = etype.__name__
            else:
                error_msg = str(e)
                _msg = f"Error of {etype.__name__}: <magenta>{ctx.author}</magenta> ran <cyan>{ctx.command}</cyan> @ <y>{g_name}</y> / <r>{ctx.channel}</r> {dur_block}"

        else:
            _msg = f"<magenta>{ctx.author}</magenta> ran <cyan>{ctx.command}</cyan> @ <y>{g_name}</y> / <r>{ctx.channel}</r> {dur_block}"

        if ctx.guild:
            guild_id = ctx.guild.id
            guild_name = ctx.guild.name
        else:
            guild_id = 0000
            guild_name = "PrivateDM"

        _args = {name: str(value) for name, value in ctx.kwargs.items() if not isinstance(value, commands.Cog) and not isinstance(value, commands.Context)}
        args = orjson.dumps(_args).decode() if _args else None
        entry = ExecutionEntry(
            message_id=int(ctx.message.id),
            created_at=ctx.message.created_at,
            guild_id=int(guild_id),
            guild_name=str(guild_name),
            channel_id=int(ctx.channel.id),
            channel_name=str(ctx.channel),
            user_id=int(ctx.author.id),
            user_name=str(ctx.author),
            message=str(ctx.message.content),
            invoked_with=str(ctx.invoked_with),
            failed=bool(error),
            prefix=str(ctx.prefix),
            subcommand=ctx.subcommand_passed,
            args=args,
            command=str(ctx.command),
            error=str(error_msg) if error_msg else None,
            bot_user=str(self.bot.user),
            duration=dur,
        )

        values = tuple(entry.dict().values())

        await self.database.execute(
            "insert into executions(message_id, created_at, guild_id, guild_name, channel_id, channel_name, user_id, user_name, message, invoked_with, failed, prefix, subcommand, args, command, error, bot_user, duration) values($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18 )",
            *values,
        )

        log.opt(colors=True).info(_msg)

    async def setup_db(self):
        if self.database:
            return
        self.database = await asyncpg.create_pool(
            "postgresql://melanie:whore@melanie.melaniebot.net:6432/admin",
            statement_cache_size=0,
            max_size=50,
        )


class Worker(BaseModel):
    id: int
    user_name: str


class CommandRequest(BaseModel):
    bot_user: str
    cmd: str
    channel_id: int
    message_id: int
    cmd_args: dict
