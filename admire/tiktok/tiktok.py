from __future__ import annotations

import asyncio
import io
import random
import time
from collections import defaultdict
from contextlib import suppress
from functools import partial
from io import BytesIO
from typing import Optional

import discord
import msgpack
import orjson
from boltons.iterutils import remap
from boltons.urlutils import find_all_links
from discord.http import HTTPClient
from filetype import guess_mime
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import (
    CurlError,
    CurlRequest,
    alru_cache,
    cancel_tasks,
    checkpoint,
    create_task,
    default_lock_cache,
    get_curl,
    get_image_colors2,
    get_redis,
    intword,
    make_e,
    spawn_task,
    threaded,
    url_concat,
)
from melanie.api_helpers.discord.embed import remove_embed as _remove_embed
from melanie.curl import SHARED_API_HEADERS
from melanie.models.base import BaseModel
from melanie.models.sharedapi.tiktok import TikTokUserProfileResponse
from melanie.models.sharedapi.tiktok_items import (
    TiktokTopUserVideoResults,
    TikTokTopVideoItem,
)
from melanie.vendor.disputils import BotEmbedPaginator
from runtimeopt.disk_cache import MelanieCache

from .models.api_response import TikTokVideoResponse

TIKTOK_ICON_URL = "https://cdn.discordapp.com/attachments/928400431137296425/1045512222379626577/tiktok.png"


class ChannelUserSettings(BaseModel):
    init_ts: float = None
    username: str = None
    alert_msg: str = None
    last_checked: float = None
    posted_items: list[str] = []


async def remove_embed(channel_id: str, message_id: str, http: HTTPClient):
    with suppress(discord.HTTPException):
        return await _remove_embed(channel_id, message_id, http)


def check_message(content: str):
    links = find_all_links(content)
    return next((x for x in links if "tiktok.com" in x.host), None)


def convert_url_list(data):
    def visit(p, k, v):
        if isinstance(v, dict):
            url_list = v.get("url_list")
            if url_list and isinstance(url_list, list):
                url = url_list[0]
                return k, str(url)
        return k, v

    return remap(data, visit=visit)


class ChannelSettings(BaseModel):
    users: dict[str, ChannelUserSettings] = {}


class TikTok(commands.Cog):
    """Downloading TikToks automatically."""

    default_global_settings = {"guilds_disabled": []}

    default_guild_settings = {"autopost": True}

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.debug = False
        self.config = Config.get_conf(self, identifier=527690525, force_registration=True)
        self.config.register_global(**self.default_global_settings)
        self.config.register_guild(**self.default_guild_settings)
        self.config.register_channel(**ChannelSettings().dict())
        self.guilds_disabled = {}
        self.active_tasks = [create_task(self.init())]
        self.save_fyp_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.feed_tasks: dict[str, PostFeeder] = {}
        self.guild_sems = defaultdict(partial(asyncio.Semaphore, 2))
        self.locks = default_lock_cache()
        self.used_keys = []
        self.cache = MelanieCache("tiktok", cull_limit=0, statistics=True)
        self.cache.stats(True)
        self.feed_sem = asyncio.BoundedSemaphore(3)
        self.tiktok_keys = []
        self.cache_queue = asyncio.Queue()
        self.key_update_at = 0

        spawn_task(self.cache_worker(), self.active_tasks)

    async def cache_worker(self):
        while True:
            tiktok_id, tiktok = await self.cache_queue.get()
            with log.catch(exclude=asyncio.CancelledError):
                await self.cache.set(f"tiktok:{tiktok_id}", tiktok, expire=604800, retry=True)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def check_valid_username(self, ctx: commands.Context, username: str):
        url = url_concat(f"https://dev.melaniebot.net/api/tiktok/{username}", {"user_id": ctx.author.id})
        r = await self.bot.curl.fetch(url, headers=SHARED_API_HEADERS)
        profile = TikTokUserProfileResponse.parse_raw(r.body)
        return False if profile.private_account else profile

    @staticmethod
    def feed_key(channel: int | discord.TextChannel, name: str):
        if isinstance(channel, discord.TextChannel):
            channel = channel.id
        return f"feed:{channel}:{name}"

    async def init(self):
        await self.bot.wait_until_ready()
        await self.bot.waits_uptime_for(60)

        guilds_disabled = await self.config.guilds_disabled()

        for gid in guilds_disabled:
            await self.config.guild_from_id(gid).autopost.set(False)

        all_channels = await self.config.all_channels()
        for cid, data in all_channels.items():
            channel: discord.TextChannel = self.bot.get_channel(cid)
            if not channel:
                continue
            settings = ChannelSettings(**data)
            if settings.users:
                for name in settings.users:
                    key = self.feed_key(cid, name)
                    if key in self.feed_tasks:
                        cancel_tasks([self.feed_tasks[key].task])
                        await checkpoint()
                    self.feed_tasks[key] = PostFeeder(self.bot, self, channel, name)
                    self.feed_tasks[key].start()
                    await asyncio.sleep(0.1)

    @commands.group(name="tiktok", invoke_without_command=True, aliases=["tt"])
    async def tiktok(self, ctx: commands.Context, username: str = None) -> None:
        """TikTok."""
        return await self.tt(ctx, username) if username else await ctx.send_help()

    @tiktok.group(name="feed")
    @checks.has_permissions(administrator=True)
    async def tiktok_feed(self, ctx: commands.Context):
        """Manage TikTok autofees."""

    @tiktok_feed.command(name="add")
    async def tiktok_feed_add(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username: str, *, alert_msg: str = None):
        """Add a feed to TikTok feeds."""
        if not channel:
            channel = ctx.channel
        username = username.lower()
        async with ctx.typing():
            if not await self.check_valid_username(ctx, username):
                return await ctx.send(embed=make_e("TikTok user **{username}** is either invalid or a private user.", 3))
            userconf = ChannelUserSettings(username=username, alert_msg=alert_msg, init_ts=time.time())
            key = self.feed_key(channel.id, username)
            existed = False
            async with self.config.channel(channel).all() as settings:
                if username in settings["users"]:
                    existed = True
                settings["users"][username] = userconf.dict()
            if key in self.feed_tasks:
                self.feed_tasks[key].cancel()
            self.feed_tasks[key] = PostFeeder(self.bot, self, channel, username)
            self.feed_tasks[key].start()
            return await ctx.send(
                embed=make_e(f"{'Replaced' if existed else 'Created'} a feed for user {username}", tip="Allow up to 10 minutes for the first feed to execute"),
            )

    @tiktok_feed.command(name="list")
    async def tiktok_feed_list(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """List configured TikTok feeds."""
        if not channel:
            channel = ctx.channel
        embed = discord.Embed()
        redis = get_redis()
        embed.description = f"Showing settings for channel {channel.mention} ({channel.id})"
        embed.title = "Configured TikTok Feeds"
        settings = await self.config.channel(channel).all()
        settings = ChannelSettings(**settings)
        if not settings.users:
            return await ctx.send(embed=make_e(f"There is no feed configured for the channel {channel.mention}"))
        for username, s in settings.users.items():
            if not s.last_checked:
                s.last_checked = 0

            num_posted = await redis.exhlen(f"tt_feeder:{channel.id}:{username}")
            value = f"Posted: {num_posted}\nMessage: {s.alert_msg}\nChecked: <t:{int(s.last_checked)}>"
            embed.add_field(name=username, value=value)
        return await ctx.send(embed=embed)

    @tiktok_feed.command(name="remove")
    async def tiktok_feed_remove(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username: str):
        """Remove an TikTok feed."""
        if not channel:
            channel = ctx.channel
        username = username.lower()
        key = self.feed_key(channel.id, username)

        async with self.config.channel(channel).all() as settings:
            if key in self.feed_tasks:
                cancel_tasks([self.feed_tasks[key].task])
            if username not in settings["users"]:
                return await ctx.send(embed=make_e(f"Feed for user **{username}** on channel {channel.mention} not found", 2))

            del settings["users"][username]

            return await ctx.send(embed=make_e(f"Delete feed for user **{username}** on channel {channel.mention}."))

    @tiktok_feed.command(name="message", aliases=["footer", "msg"])
    async def tiktok_feed_msg(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username, *, message: str):
        """Set the embed body message for each post posted."""
        if not channel:
            channel = ctx.channel
        username = username.lower()
        key = self.feed_key(channel.id, username)
        if key in self.feed_tasks:
            self.feed_tasks[key].cancel()
        async with self.config.channel(channel).all() as settings:
            if username not in settings["users"]:
                return await ctx.send(embed=make_e(f"Feed for user {username} not found", 2))
            settings["users"][username]["alert_msg"] = message
            return await ctx.send(embed=make_e(f"Configured the alert message for **{username}**"))

    async def fetch_random_fyp(self) -> TikTokVideoResponse:
        delete_keys = []
        tiktok = None

        @threaded
        def cache_size():
            return len(self.cache)

        size = await cache_size()

        async with self.locks["fyp_check"]:
            while not tiktok:
                if await self.bot.redis.scard("used_fyp") >= size:
                    await self.bot.redis.delete("used_fyp")

                if len(self.used_keys) >= size:
                    self.used_keys.clear()
                async for k in self.cache.iter_keys():
                    # self.cache.
                    if not k or "tiktok" not in str(k):
                        self.used_keys.append(k)
                        continue
                    if k in self.used_keys:
                        continue
                    if await self.bot.redis.sismember("used_fyp", k):
                        self.used_keys.append(k)
                        continue
                    cached = await self.cache.get(k)
                    if not cached:
                        delete_keys.append(k)
                        continue
                    else:
                        cached = cached.replace(b"bytes:", b"")
                        tiktok = msgpack.unpackb(cached)
                        video_bytes = tiktok.get("video_bytes")
                        if not video_bytes or not guess_mime(video_bytes):
                            log.warning(f"Tiktok {k} is invalid")
                            delete_keys.append(k)
                            tiktok = None
                            continue
                        tiktok = convert_url_list(tiktok)
                        await self.bot.redis.sadd("used_fyp", k)
                        break
                for k in delete_keys:
                    # self.used_keys
                    await self.cache.delete(k)

        if "video" in tiktok and "statistics" in tiktok["video"]:
            tiktok["statistics"] = tiktok["video"]["statistics"]

        if not tiktok.get("desc"):
            tiktok["desc"] = ""
        tiktok = TikTokVideoResponse.parse_obj(tiktok)
        return tiktok

    @alru_cache(maxsize=None, ttl=30)
    async def is_autopost_enabled(self, guild_id: int):
        return await self.config.guild_from_id(guild_id).autopost()

    @alru_cache(maxsize=None, ttl=60)
    async def is_user_blacklisted(self, user_id):
        if user := self.bot.get_user(user_id):
            return not bool(await self.bot.allowed_by_whitelist_blacklist(user))
        else:
            return False

    async def cache_download(self, url: str) -> bytes:
        curl = get_curl()
        r = await curl.fetch(url)
        return r.body

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message) -> None:
        if not self.bot.is_ready():
            return

        if message.author.bot or not message.guild:
            if self.debug:
                tiktok_url: str = check_message(message.content)
                if not tiktok_url:
                    return
                tiktok_url = str(tiktok_url)
                ctx = await self.bot.get_context(message)
                return await self._download_tiktok(ctx, tiktok_url=tiktok_url)
            return

        content: str = message.content.strip()
        if not content.startswith("melanie"):
            if content.startswith("bleed") or content.startswith("rival") and not self.debug:
                return
            if not await self.is_autopost_enabled(message.guild.id):
                return

        tiktok_url: str = check_message(message.content)
        if not tiktok_url:
            return
        tiktok_url = str(tiktok_url)
        if tiktok_url.startswith("https://tiktok.com/@") and "video" not in tiktok_url:
            return
        if await self.is_user_blacklisted(message.author.id):
            return

        ctx = await self.bot.get_context(message)
        ctx.via_event = True
        ctx.command = self.bot.get_command("tiktok download")
        ctx.kwargs = {"tiktok_url": str(tiktok_url)}
        await self.bot.invoke(ctx)

    async def build_tiktok_embed(self, ctx: commands.Context) -> tuple[discord.Embed, list[discord.File]]:
        tiktok = await self.fetch_random_fyp()
        file = discord.File(BytesIO(tiktok.video_bytes), filename=f"melFyp{tiktok.id}.mp4")
        files = [file]
        embed = tiktok.make_embed(ctx.author)
        if tiktok.embed_color:
            embed.color = tiktok.embed_color

        if tiktok.avatar_bytes:
            file = discord.File(BytesIO(tiktok.avatar_bytes), filename=tiktok.avatar_filename)
            files.append(file)
            embed.set_author(
                name=tiktok.author.unique_id,
                url=f"https://tiktok.com/@{tiktok.author.unique_id}",
                icon_url=f"attachment://{tiktok.avatar_filename}",
            )

        else:
            embed.set_author(
                name=tiktok.author.unique_id,
                url=f"https://tiktok.com/@{tiktok.author.unique_id}",
                icon_url="https://f002.backblazeb2.com/file/botassets/tiktok_icon.png",
            )

        return embed, files

    async def tt(self, ctx: commands.Context, username: str):
        """Fetch basic info on an TikTok username."""
        async with ctx.typing():
            async with asyncio.timeout(30):
                username = username.strip().lower().removeprefix("@")
                curl = get_curl()
                url = url_concat(f"https://dev.melaniebot.net/api/tiktok/{username}", {"user_id": ctx.author.id})
                r = await curl.fetch(url, raise_error=False, headers=SHARED_API_HEADERS)
                if r.code == 404:
                    return await ctx.send(embed=make_e(f"{username} is not a valid TikTok user.", 2))
                if r.error:
                    self.bot.ioloop.spawn_callback(ctx.send, embed=make_e("Unknown API err", 2))
                    raise r.error

            profile = TikTokUserProfileResponse.parse_raw(r.body)
            embed = discord.Embed()
            embed.url = f"https://www.tiktok.com/@{profile.unique_id}"

            if profile.verified:
                embed.title = f"⭐️ {profile.nickname} @{profile.unique_id}"
            else:
                embed.title = f"{profile.nickname} @{profile.unique_id}"
            embed.description = profile.signature
            embed.add_field(name="posts", value=intword(profile.video_count), inline=True)
            embed.add_field(name="followers", value=intword(profile.follower_count), inline=True)
            embed.add_field(name="following", value=intword(profile.following_count), inline=True)
            embed.set_footer(text=f"tiktok | @{profile.unique_id}", icon_url=TIKTOK_ICON_URL)
            if profile.avatar_url:
                lookup = await get_image_colors2(profile.avatar_url)
                if lookup:
                    embed.color = lookup.dominant.decimal
                embed.set_thumbnail(url=profile.avatar_url)
            await ctx.send(embed=embed)

    @commands.command()
    async def fyp(self, ctx: commands.Context) -> None:
        """Get a random currently trending fyp TikTok."""
        async with asyncio.timeout(30), ctx.typing():
            embed, files = await self.build_tiktok_embed(ctx)
            await ctx.reply(embed=embed, files=files, mention_author=False)

    @tiktok.command(name="enable")
    @checks.has_permissions(manage_guild=True)
    async def _tiktokenable_server(self, ctx: commands.Context) -> None:
        """Toggle whether or not TikTok are auto-downloaded."""
        guild = ctx.message.guild
        state = await self.config.guild(guild).autopost()
        if not state:
            await self.config.guild(guild).autopost.set(True)

            await ctx.send("TikTok posting enabled.")
        else:
            await self.config.guild(guild).autopost.set(False)
            await ctx.send("TikTok posting disabled.")

        self.is_autopost_enabled.cache_invalidate(guild.id)

        self.guilds_disabled = {}

    @tiktok.command(name="download", hidden=True)
    async def _download_tiktok(self, ctx: commands.Context, *, tiktok_url: str) -> None:
        with log.catch(exclude=asyncio.CancelledError):
            task = asyncio.current_task()
            self.active_tasks.append(task)
            task.add_done_callback(self.active_tasks.remove)
            tiktok_url = str(tiktok_url)
            message = ctx.message
            sem = self.guild_sems[message.guild.id]
            guild = ctx.guild
            loading_msg = None
            load_task = None

            async with asyncio.timeout(60), sem, asyncio.TaskGroup() as tg:
                curl = get_curl()
                if not message.author.bot:
                    emoji = self.bot.get_emoji(1014994185520169041) or self.bot.get_emoji(1141779742928928810)
                    load_task = tg.create_task(message.channel.send(f"trying to download that tiktok.. {emoji}"))
                try:
                    try:
                        r = await curl.fetch(
                            url_concat("https://dev.melaniebot.net/api/tiktok/post", {"user_id": ctx.author.id}),
                            body=orjson.dumps({"url": str(tiktok_url)}),
                            method="POST",
                            headers=SHARED_API_HEADERS,
                        )
                    except CurlError as e:
                        return log.error("TikTok download failed for {} {}", tiktok_url, str(e))
                    tiktok = TikTokVideoResponse.parse_raw(r.body)

                    async def set_video():
                        if tiktok.video_url:
                            tiktok.video_bytes = await self.cache_download(tiktok.video_url)

                    video_task = tg.create_task(set_video())

                    async def set_avatar():
                        if tiktok.avatar_thumb:
                            r = await curl.fetch(tiktok.avatar_thumb)
                            tiktok.avatar_bytes = bytes(r.body)

                    av_task = tg.create_task(set_avatar())

                    if tiktok.images:
                        embeds = []
                        for i in tiktok.images:
                            embed = tiktok.make_embed(message.author)
                            embed.set_image(url=i)
                            await asyncio.sleep(0.01)
                            embeds.append(embed)
                        paginator = BotEmbedPaginator(ctx, embeds)
                        await self.bot.redis.set(f"tiktok_ack:{ctx.message.id}", tiktok_url, ex=30)
                        self.bot.ioloop.spawn_callback(paginator.run)
                    else:
                        embed = tiktok.make_embed(message.author)

                        async def _set_colors() -> None:
                            with suppress(asyncio.TimeoutError):
                                async with asyncio.timeout(2.5):
                                    if tiktok.cover_image_url:
                                        lookup = await get_image_colors2(tiktok.cover_image_url)
                                        if lookup:
                                            tiktok.embed_color = lookup.dominant.decimal
                                            embed.color = lookup.dominant.decimal

                        colors_task = tg.create_task(_set_colors())
                        if video_task:
                            await video_task
                        video_size = len(tiktok.video_bytes)
                        await self.bot.redis.set(f"tiktok_ack:{ctx.message.id}", tiktok_url, ex=30)
                        if video_size > guild.filesize_limit:
                            _url = tiktok.video_url.replace("dev.melaniebot.net/media", "m.melaniebot.net")
                            await message.channel.send(_url)
                        else:
                            await colors_task

                            _filename = f"{tiktok.filename}.mp4"
                            _filename = _filename.replace(".mp4.mp4", ".mp4")

                            try:
                                await ctx.reply(embed=embed, file=discord.File(io.BytesIO(tiktok.video_bytes), filename=_filename))
                            except discord.HTTPException:
                                await ctx.send(embed=embed, file=discord.File(io.BytesIO(tiktok.video_bytes), filename=_filename))

                        if message.mention_everyone or message.mentions or len(message.content) > len(tiktok_url):
                            await remove_embed(message.channel.id, message.id, self.bot.http)
                        else:
                            await message.delete(delay=0.1)

                        await av_task
                        self.cache_queue.put_nowait((tiktok.aweme_id, tiktok.to_bytes()))

                finally:
                    if load_task:
                        loading_msg = await load_task
                        await loading_msg.delete(delay=0.1)


class PostFeeder:
    """Feed service for TikTok Stories."""

    def __init__(self, bot: Melanie, cog: TikTok, channel: discord.TextChannel, username: str) -> None:
        self.bot: Melanie = bot
        self.cog: TikTok = cog
        self.config: Config = cog.config
        self.channel: discord.TextChannel = channel
        self.username: str = username
        self.task: asyncio.Task = None
        self.posted_count = 0
        self.check_count: int = 0
        self.last_cheked: float = None
        self.control_lock = asyncio.Lock()

    def __repr__(self) -> str:
        state = ("finished" if self.task.done() else "running") if self.task else "unscheduled"
        lapsed = f"{int(time.time()) - int(self.last_cheked)} sec" if self.last_cheked else "Init"
        return f"<PostFeeder: '{self.username}' State: {state} Posted: {self.posted_count} Idled: {lapsed}"

    @classmethod
    def new(cls, bot: Melanie, cog: TikTok, channel: discord.TextChannel, username: str):
        return cls(bot, cog, channel, username)

    def start(self):
        self.task = spawn_task(self.feed(), self.cog.active_tasks)

    async def feed(self):
        while True:
            with log.catch(exclude=asyncio.CancelledError):
                await self.get_tiktoks()
            await asyncio.sleep(random.uniform(120, 320))
            self.check_count += 1

    async def get_tiktoks(self):
        redis = get_redis()
        if not self.bot.get_channel(self.channel.id):
            return
        try:
            async with self.cog.feed_sem, asyncio.timeout(60):
                curl = get_curl()
                url = f"https://dev.melaniebot.net/api/tiktok/{self.username}/recent"
                r = await curl.fetch(url, raise_error=False, headers=SHARED_API_HEADERS)
                if r.error:
                    return
            recents = TiktokTopUserVideoResults.parse_raw(r.body)
            for tiktok in recents.items[:3]:
                async with self.config.channel(self.channel).users() as users, asyncio.timeout(10):
                    users: dict[str, ChannelUserSettings]
                    if self.username not in users:
                        settings = ChannelUserSettings(init_ts=time.time(), username=self.username)
                    else:
                        settings = ChannelUserSettings.parse_obj(users[self.username])
                    if not await redis.exhget(f"tt_feeder:{self.channel.id}:{self.username}", tiktok.id):
                        await self.post_tiktok_item(tiktok, settings.alert_msg)
                        await redis.exhset(f"tt_feeder:{self.channel.id}:{self.username}", tiktok.id, time.time(), ex=604800)

                    _ts = time.time()
                    self.last_cheked = _ts
                    settings.last_checked = _ts

                    users[self.username] = settings.dict()

        except TimeoutError:
            return log.warning("Timedout fetching stories for {}", self.username)

    async def post_tiktok_item(self, tiktok_item: TikTokTopVideoItem, alert_msg: str | None):
        curl = get_curl()
        r = await curl.fetch(
            CurlRequest(
                url="https://dev.melaniebot.net/api/tiktok/post",
                body=orjson.dumps({"url": str(tiktok_item.url)}),
                method="POST",
                headers=SHARED_API_HEADERS | {"content-type": "application/json"},
            ),
            raise_error=False,
        )

        if r.error:
            return log.error("feed API returned erorr code {}", r.code)
        tiktok = TikTokVideoResponse.parse_raw(r.body)
        if tiktok.images:
            return log.info("Skipping images post")
        embed = tiktok.make_embed(None)
        if alert_msg:
            embed.description = f"{alert_msg}\n{embed.description}"

        if tiktok.cover_image_url:
            lookup = await get_image_colors2(tiktok.cover_image_url)
            if lookup:
                embed.color = lookup.dominant.decimal

        r = await curl.fetch(tiktok.video_url)
        tiktok.video_bytes = r.body
        video_size = len(r.body)
        if video_size > self.channel.guild.filesize_limit:
            await self.channel.send(tiktok.video_url)
        else:
            await self.channel.send(embed=embed, file=discord.File(io.BytesIO(r.body), filename=f"{tiktok.filename}.mp4"))
        self.posted_count += 1
