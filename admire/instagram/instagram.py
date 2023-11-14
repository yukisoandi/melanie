from __future__ import annotations

import asyncio
import random
import sys
import textwrap
import time
from collections import defaultdict
from contextlib import nullcontext, suppress
from io import BytesIO
from typing import Optional

import arrow
import discord
import regex as re
from anyio import Path as AsyncPath
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from tornado.httputil import url_concat

from melanie import (
    BaseModel,
    CurlError,
    aiter,
    cancel_tasks,
    checkpoint,
    create_task,
    default_lock_cache,
    find_all_links,
    footer_gif,
    get_api_baseurl,
    get_curl,
    get_filename_from_url,
    get_image_colors2,
    hex_to_int,
    intword,
    make_e,
    spawn_task,
    url_to_mime,
    yesno,
)
from melanie.api_helpers.discord.embed import remove_embed
from melanie.curl import SHARED_API_HEADERS, CurlRequest
from melanie.helpers import get_image_colors2
from melanie.models.sharedapi.instagram import (
    HighlightItem,
    InstagramHighlightIndexResponse,
    InstagramHighlightResponse,
    InstagramPostRequest,
    InstagramPostResponse,
    InstagramProfileModelResponse,
    InstagramStoryResponse,
    InstagramUserResponse,
    StoryItem,
    UserPostItem,
)
from melanie.timing import capturetime
from melanie.vendor.disputils import BotEmbedPaginator

SESSION_ROOT = "instagramsessions"


IG_COLOR_HEX = hex_to_int("#C13584")
IG_ICON_URL = "https://www.instagram.com/static/images/ico/favicon-192.png/68d99ba29cc8.png"
IG_RE = re.compile(r"(?:https?:\/\/)?(?:www.)?instagram.com\/?([a-zA-Z0-9\.\_\-]+)?\/(;+)?([reel]+)?([tv]+)?([stories]+)?\/([a-zA-Z0-9\-\_\.]+)\/?([0-9]+)?")


def parse_story_link(url: str) -> tuple[str, str]:
    search = IG_RE.findall(url)[0]
    username = search[5]
    story_ident = search[6]
    return username, story_ident


class ChannelUserSettings(BaseModel):
    init_ts: float = None
    username: str = None
    alert_msg: str = None


class ChannelSettings(BaseModel):
    users: dict[str, ChannelUserSettings] = {}


async def set_color(e: discord.Embed, img_url: str, timeout_duration: float = 2.0):
    with suppress(asyncio.TimeoutError):
        async with asyncio.timeout(timeout_duration):
            lookup = await get_image_colors2(img_url)
            if lookup:
                e.color = lookup.dominant.decimal


class StoryFeeder:
    """Feed service for Instagram Stories."""

    def __init__(self, bot: Melanie, cog: Instagram, channel: discord.TextChannel, username: str) -> None:
        self.bot: Melanie = bot
        self.cog: Instagram = cog
        self.config: Config = cog.config
        self.channel: discord.TextChannel = channel
        self.username: str = username
        self.task: asyncio.Task = None
        self.posted_count = 0
        self.check_count: int = 0
        self.last_cheked: float = None
        self.control_lock = asyncio.Lock()
        self.run_count = 0
        self.feed_lock = self.bot.redis.get_lock(f"feedchannel:{self.channel.id}", timeout=90) if self.channel else nullcontext()

    def __repr__(self) -> str:
        state = ("finished" if self.task.done() else "running") if self.task else "unscheduled"
        lapsed = f"{int(time.time()) - int(self.last_cheked)} sec" if self.last_cheked else "Init"
        return f"<StoryFeeder: '{self.username}' State: {state} Posted: {self.posted_count} Idled: {lapsed}"

    @classmethod
    def new(cls, bot: Melanie, cog: Instagram, channel: discord.TextChannel, username: str):
        return cls(bot, cog, channel, username)

    def cancel(self):
        return cancel_tasks([self.task])

    def start(self):
        self.task = spawn_task(self.feed(), self.cog.active_tasks)

    async def feed(self):
        while True:
            with log.catch(exclude=asyncio.CancelledError):
                await self.get_stories()
            with log.catch(exclude=asyncio.CancelledError):
                await self.get_posts()
            self.check_count += 1
            self.run_count += 1
            await asyncio.sleep(random.uniform(60, 120))

    async def get_stories(self):
        if not self.channel or not self.bot.get_channel(self.channel.id):
            return

        async with asyncio.timeout(90):
            curl = get_curl()
            url = f"https://dev.melaniebot.net/api/instagram/story/{self.username}"
            r = await curl.fetch(url, raise_error=False, headers=SHARED_API_HEADERS)
            if r.error:
                return
            stories = InstagramStoryResponse.parse_raw(r.body)
            await self.config.custom("FEED", str(self.channel.id), self.username).last_checked_story.set(int(stories.created_at))
        async with self.config.custom("FEED", str(self.channel.id), self.username).items() as posted_items:
            channel_settings = await self.config.channel(self.channel).all()
            channel_settings: ChannelSettings = ChannelSettings.parse_obj(channel_settings)
            users = channel_settings.users
            if self.username not in users:
                settings = ChannelUserSettings(init_ts=time.time(), username=self.username)
            else:
                settings = ChannelUserSettings.parse_obj(users[self.username])
            for story in stories.items:
                if story.id in posted_items:
                    continue
                with suppress(CurlError):
                    await self.post_story_item(story, stories.author, settings.alert_msg)
                    posted_items.append(story.id)

    async def get_posts(self):
        if not self.channel or not self.bot.get_channel(self.channel.id):
            return

        curl = get_curl()
        try:
            url = f"https://dev.melaniebot.net/api/instagram/{self.username}"
            r = await curl.fetch(url, headers=SHARED_API_HEADERS)
        except CurlError:
            return
        profile = InstagramProfileModelResponse.parse_raw(r.body)
        await self.config.custom("FEED", str(self.channel.id), self.username).last_checked_posts.set(int(profile.created_at))
        async with self.config.custom("FEED", str(self.channel.id), self.username).items() as posted_items:
            channel_settings = await self.config.channel(self.channel).all()
            channel_settings: ChannelSettings = ChannelSettings.parse_obj(channel_settings)
            users = channel_settings.users
            if self.username not in users:
                settings = ChannelUserSettings(init_ts=time.time(), username=self.username)
            else:
                settings = ChannelUserSettings.parse_obj(users[self.username])
            for post in profile.post_items[:3]:
                if post.id in posted_items:
                    continue
                with suppress(CurlError):
                    await self.post_post_item(post, settings.alert_msg)
                    posted_items.append(post.id)

    async def post_post_item(self, post: UserPostItem, alert_msg: str):
        curl = get_curl()
        payload = InstagramPostRequest(content=post.url, user_id=self.channel.id, guild_id=self.channel.guild.id)
        r = await curl.fetch(
            "https://dev.melaniebot.net/api/instagram/post",
            headers=SHARED_API_HEADERS,
            body=payload.jsonb(),
            method="POST",
        )
        pi = InstagramPostResponse.parse_raw(r.body)
        target_video_url = None
        target_video_filename = None
        target_caption = ""

        for item in pi.items:
            if item.video_url:
                target_video_url = item.video_url
                target_video_filename = item.video_filename

            elif item.image_url:
                target_video_url = item.image_url
                target_video_filename = item.image_filename

            elif item.sidecars:
                target_video_url = item.sidecars[0].url
                target_video_filename = item.sidecars[0].filename

            if item.caption and item.caption.text:
                target_caption = item.caption.text
            embed = pi.make_embed()
            if item.preview_image_url:
                lookup = await get_image_colors2(item.preview_image_url)
                if lookup:
                    embed.color = lookup.dominant.decimal
            embed.description = ""
            embed.title = f"{pi.clean_caption(textwrap.shorten(target_caption,250))}"
            embed.url = pi.share_url
            if alert_msg:
                embed.description = f"{alert_msg}\n\n{embed.description}"
            r = await curl.fetch(target_video_url)
            if len(r.body) > self.channel.guild.filesize_limit:
                continue
            await self.channel.send(
                embed=embed,
                file=discord.File(
                    BytesIO(r.body),
                    filename=target_video_filename,
                ),
            )

    async def post_story_item(self, story_item: StoryItem, author: InstagramUserResponse, alert_msg: str | None):
        curl = get_curl()
        r = await curl.fetch(story_item.url, raise_error=False)
        if r.error:
            return log.error("Story API returned erorr code {}", r.code)
        embed = discord.Embed()
        if alert_msg:
            embed.description = alert_msg
        embed.timestamp = arrow.get(story_item.taken_at).datetime
        embed.url = f"https://www.instagram.com/stories/{author.username}/{story_item.id}/"
        mime = url_to_mime(story_item.url)
        lookup_url = story_item.url if mime and "image" in mime else author.avatar_url
        lookup = await get_image_colors2(lookup_url)
        if lookup:
            embed.color = lookup.dominant.decimal
        embed.set_author(name=author.username, icon_url=author.avatar_url, url=f"https://instagram.com/{author.username}")
        embed.set_footer(text="instagram story", icon_url=IG_ICON_URL)
        await self.channel.send(embed=embed, file=discord.File(r.buffer, story_item.filename))
        self.posted_count += 1


class Instagram(commands.Cog):
    """Interaction with Instagram."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.debug = False
        self.locks = defaultdict(asyncio.Lock)
        self.story_channel_locks = default_lock_cache()
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.config.register_channel(**ChannelSettings().dict())
        self.config.init_custom("FEED", 2)
        self.config.register_custom("FEED", items=[], last_checked_posts=None, last_checked_story=None)
        self.story_tasks: dict[str, StoryFeeder] = {}
        self.active_tasks: list[asyncio.Task] = [create_task(self.init())]
        self.feed_sem = asyncio.BoundedSemaphore(2)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def get_total_guild_feeds(self, guild: discord.Guild):
        count = 0
        for channel in guild.channels:
            if isinstance(channel, discord.TextChannel):
                users = await self.config.channel(channel).users()
                count += len(users)
        return count

    @staticmethod
    def story_key(channel: int | discord.TextChannel, name: str):
        if isinstance(channel, discord.TextChannel):
            channel = channel.id
        return f"story:{channel}:{name}"

    async def init(self):
        await self.bot.wait_until_ready()
        await self.bot.waits_uptime_for(60)
        all_channels = await self.config.all_channels()
        for cid, data in all_channels.items():
            channel: discord.TextChannel = self.bot.get_channel(cid)
            settings = ChannelSettings(**data)
            if settings.users:
                for name in settings.users:
                    key = self.story_key(cid, name)
                    if key in self.story_tasks:
                        cancel_tasks([self.story_tasks[key].task])
                    self.story_tasks[key] = StoryFeeder(self.bot, self, channel, name)
                    self.story_tasks[key].start()
                    await asyncio.sleep(0.01)

    async def user_lookup(self, ctx: commands.Context, username: str):
        """Fetch basic info on an Instagram username."""
        async with ctx.typing():
            async with asyncio.timeout(30):
                username = username.strip().lower().removeprefix("@")

                params = {"user_id": ctx.author.id}

                curl = get_curl()

                profile_task = curl.fetch(
                    url_concat(f"https://dev.melaniebot.net/api/instagram/{username}", params),
                    headers=SHARED_API_HEADERS,
                    raise_error=False,
                )

                r = await profile_task
                if r.code == 404:
                    return await ctx.send(embed=make_e(f"{username} is not a valid instagram user.", 2))
                if r.code == 429:
                    return await ctx.send(embed=make_e("You've been rate limited from username lookups for now. Please try later", 2))
                if r.error:
                    raise r.error
            profile: InstagramProfileModelResponse = InstagramProfileModelResponse.parse_raw(r.body)
            if not profile.is_private:
                _url = get_api_baseurl("api", "instagram", "story", username)
                stories_task = curl.fetch(_url, headers=SHARED_API_HEADERS, raise_error=False)
            else:
                stories_task = None
            embed = discord.Embed()
            embed.url = f"https://instagram.com/{username}"
            if profile.is_private:
                embed.title = f"🔐 {profile.full_name} @{profile.username}"
            elif profile.is_verified:
                embed.title = f"⭐️ {profile.full_name} @{profile.username}"
            else:
                embed.title = f"{profile.full_name} @{profile.username}"
            embed.description = profile.biography
            embed.add_field(name="posts", value=intword(profile.post_count), inline=True)
            embed.add_field(name="followers", value=intword(profile.followed_by_count), inline=True)
            embed.add_field(name="following", value=intword(profile.following_count), inline=True)
            embed.set_footer(text="instagram", icon_url=IG_ICON_URL)
            if profile.avatar_url:
                embed.set_thumbnail(url=profile.avatar_url)
                embed.color = IG_COLOR_HEX
        await ctx.send(embed=embed)
        if stories_task:
            r = await stories_task
            if not r.error:
                stories = InstagramStoryResponse.parse_raw(r.body)
                if stories.items:
                    load_stories, _msg = await yesno(
                        f"**{username}** has an active story.",
                        "Do you want to view it?",
                        timeout=20,
                        delete_delay=30,
                        ok_title="Confirmed!",
                        ok_body=f"There are a total of {len(stories.items)} story items loaded",
                    )
                if load_stories:
                    await self.igstory_task(ctx, username, stories)

    @commands.command()
    async def igpost(self, ctx: commands.Context, username: str, index: int = 1):
        curl = get_curl()

        async with ctx.typing():
            async with asyncio.timeout(40):
                r = await curl.fetch(
                    url_concat(f"https://dev.melaniebot.net/api/instagram/{username}", {"user_id": ctx.author.id}),
                    headers=SHARED_API_HEADERS,
                )
                data = InstagramProfileModelResponse.parse_raw(r.body)
                if data.is_private:
                    return await ctx.send(embed=make_e(f"User **{username}** is private", 2))

                if not data.post_items:
                    return await ctx.send(embed=make_e(f"User **{username}** has no recent posts", 2))

                try:
                    post = data.post_items[index - 1]
                except IndexError:
                    return await ctx.send(embed=make_e(f"User **{username}** has no post at index **{index}**", 2))
                payload = InstagramPostRequest(content=post.url, user_id=ctx.author.id, guild_id=ctx.guild.id)
                r = await curl.fetch("https://dev.melaniebot.net/api/instagram/post", headers=SHARED_API_HEADERS, body=payload.jsonb(), method="POST")
                pi = InstagramPostResponse.parse_raw(r.body)
                target_video_url = None
                target_video_filename = None
                target_caption = ""
                embeds = []
                for item in pi.items:
                    embed = pi.make_embed()
                    embed.description = ""
                    embed.url = pi.share_url

                    if item.video_url:
                        target_video_url = item.video_url
                        target_video_filename = item.video_filename
                        continue

                    elif item.image_url:
                        embed.set_image(url=item.image_url)

                    elif item.sidecars:
                        embed.set_image(url=item.sidecars[0].url)

                    if item.caption and item.caption.text:
                        target_caption = item.caption.text
                    embed.title = f"{pi.clean_caption(textwrap.shorten(target_caption,250))}"
                    embeds.append(embed)

                    await checkpoint()

            if target_video_url and not embeds:
                r = await curl.fetch(target_video_url)
                if len(r.body) > ctx.guild.filesize_limit:
                    return
                await ctx.send(embed=embed, file=discord.File(BytesIO(r.body), filename=target_video_filename))

            else:
                paginator = BotEmbedPaginator(ctx, embeds)
                self.bot.ioloop.add_callback(paginator.run)

    @commands.group(name="ig", invoke_without_command=True, aliases=["instagram", "gram"])
    async def ig(self, ctx: commands.Context, username: Optional[str] = None) -> None:
        """Instagram."""
        return await self.user_lookup(ctx, username) if username else await ctx.send_help()

    @ig.group(name="feed")
    @checks.has_permissions(manage_channels=True)
    async def ig_feed(self, ctx: commands.Context):
        """Manage Instagram autofees."""

    @ig_feed.command(name="add")
    async def ig_feed_add(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username: str, *, alert_msg: str = None):
        """Add a feed to Instagram feeds."""
        async with ctx.typing():
            if not channel:
                channel = ctx.channel

            username = username.lower()

            r = await self.bot.curl.fetch(f"https://dev.melaniebot.net/api/instagram/{username}", headers=SHARED_API_HEADERS)
            profile = InstagramProfileModelResponse.parse_raw(r.body)
            if profile.is_private and not ctx.bot_owner:
                return await ctx.send(embed=make_e(f"Instagram user **{username}** is a private user.", 3))
            userconf = ChannelUserSettings(username=username, alert_msg=alert_msg, init_ts=time.time())
            key = self.story_key(channel.id, username)
            existed = False
            async with self.config.channel(channel).all() as settings:
                if username in settings["users"]:
                    existed = True
                settings["users"][username] = userconf.dict()
            if key in self.story_tasks:
                self.story_tasks[key].cancel()
            self.story_tasks[key] = StoryFeeder(self.bot, self, channel, username)
            self.story_tasks[key].start()
            return await ctx.send(
                embed=make_e(
                    f"{'Replaced' if existed else 'Created'} a feed for user **{username}**",
                    tip="Allow up to 10 minutes for the first feed to execute",
                ),
            )

    @ig_feed.command(name="list")
    async def ig_feed_list(self, ctx: commands.Context):
        """List configured Instagram feeds."""
        embed = discord.Embed()
        embed.description = "Server instagram feeds"
        embed.title = "Configured Instagram Feeds"
        channels: dict[discord.TextChannel, ChannelSettings] = {}

        for ch in ctx.guild.channels:
            if isinstance(ch, discord.TextChannel):
                settings = await self.config.channel(ch).all()
                settings = ChannelSettings(**settings)
                if settings.users:
                    channels[ch] = settings

        if not channels:
            return await ctx.send(embed=make_e(f"There is no feed configured for the channel {channel.mention}"))

        for channel, settings in channels.items():
            for username, s in settings.users.items():
                last_checked = await self.config.custom("FEED", str(channel.id), username).last_checked_posts()
                _posted = await self.config.custom("FEED", str(channel.id), username).items()
                num_posted = len(_posted)
                value = f"Posted: {num_posted}\nMessage: {s.alert_msg}\nChecked: <t:{int(last_checked)}:R>"
                embed.add_field(name=f"{channel.mention} | {username}", value=value)
        return await ctx.send(embed=embed)

    @ig_feed.command(name="remove", aliases=["del", "rm"])
    async def ig_feed_remove(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username: str):
        """Remove an Instagram feed."""
        if not channel:
            channel = ctx.channel
        username = username.lower()
        key = self.story_key(channel.id, username)
        if key in self.story_tasks:
            self.story_tasks[key].cancel()

        async with self.config.channel(channel).all() as settings:
            if username not in settings["users"]:
                return await ctx.send(embed=make_e(f"Feed for user **{username}** on channel {channel.mention} not found", 2))

            del settings["users"][username]

            return await ctx.send(embed=make_e(f"Deleted feed for user **{username}** on channel {channel.mention}."))

    @ig_feed.command(name="message", aliases=["footer", "msg"])
    async def ig_feed_msg(self, ctx: commands.Context, channel: Optional[discord.TextChannel], username, *, message: str):
        """Set the embed body message for each story posted."""
        if not channel:
            channel = ctx.channel
        username = username.lower()
        key = self.story_key(channel.id, username)
        if key in self.story_tasks:
            self.story_tasks[key].cancel()
        async with self.config.channel(channel).all() as settings:
            if username not in settings["users"]:
                return await ctx.send(embed=make_e(f"Feed for user {username} not found", 2))
            settings["users"][username]["alert_msg"] = message
            return await ctx.send(embed=make_e(f"Configured the alert message for **{username}**"))

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        # sourcery skip: remove-unreachable-code

        if not await self.bot.allowed_by_whitelist_blacklist(message.author):
            return
        guild: discord.Guild = message.guild
        if message.author.bot and not self.debug:
            return
        content: str = message.content.strip()
        if content.startswith("bleed"):
            return

        links = find_all_links(message.content)
        url = next((x for x in links if "instagram.com" in x.host or "threads.net" in x.host), None)
        if not url:
            return
        url = str(url)

        with log.catch(reraise=True):
            async with asyncio.timeout(60):
                spawn_task(remove_embed(message.channel.id, message.id, self.bot.http), self.active_tasks)
                if message.author.id not in self.bot.owner_ids and not self.debug:
                    redis_key = f"igvid:{message.author.id}"
                    if await self.bot.redis.ratelimited(redis_key, 3, 60):
                        return log.warning("Ratelimited for  {} @ {}", message.author, message.guild)
                curl = get_curl()
                async with self.locks[message.content]:
                    with capturetime(f"IG: {message.guild}/{message.author}"):
                        ctx: commands.Context = await self.bot.get_context(message)
                        async with ctx.typing():
                            if "/stories" in url and "/stories/highlights" not in url:
                                username, media_id = parse_story_link(url)
                                r = await curl.fetch(f"https://dev.melaniebot.net/api/instagram/story/{username}", headers=SHARED_API_HEADERS)
                                story = InstagramStoryResponse.parse_raw(r.body)
                                si = next((item for item in story.items if item.id == media_id), None)
                                if not si:
                                    log.warning("Retrying with force")
                                r = await curl.fetch(f"https://dev.melaniebot.net/api/instagram/story/{username}?force=true", headers=SHARED_API_HEADERS)
                                story = InstagramStoryResponse.parse_raw(r.body)
                                si = next((item for item in story.items if item.id == media_id), None)
                                if not si:
                                    return log.warning("Invalid story link for {}", url)
                                em = discord.Embed()
                                em.color = 1
                                if si.taken_at:
                                    em.timestamp = arrow.get(si.taken_at).naive
                                if story.author.username and story.author.avatar_url:
                                    em.set_author(name=story.author.username, icon_url=story.author.avatar_url)
                                em.set_footer(text="melanie | instagram stories", icon_url=footer_gif)
                                try:
                                    if si.is_video:
                                        spawn_task(set_color(em, story.author.avatar_url), self.active_tasks)
                                        await asyncio.sleep(0.01)
                                        r = await curl.fetch(si.url)
                                        return await ctx.send(embed=em, file=discord.File(r.buffer, filename=si.filename))

                                    else:
                                        _t = spawn_task(set_color(em, si.url, timeout_duration=1.2), self.active_tasks)
                                        em.set_image(url=si.url)
                                        em.description = f"[Story]({url}) requested by {message.author.mention}\n"
                                        await _t
                                        return await ctx.send(embed=em)

                                finally:
                                    etype, e, tb = sys.exc_info()
                                    if not etype and (not message.mentions and not message.mention_everyone and not message.role_mentions):
                                        await message.delete(delay=0.1)

                            else:
                                payload = InstagramPostRequest(content=message.content, user_id=message.author.id, guild_id=message.guild.id)
                                curl = get_curl()
                                r = await curl.fetch(
                                    "https://dev.melaniebot.net/api/instagram/post",
                                    headers=SHARED_API_HEADERS,
                                    body=payload.jsonb(),
                                    method="POST",
                                )

                                post = InstagramPostResponse.parse_raw(r.body)
                                has_video = None
                                target_video_url = None
                                target_video_filename = None
                                target_preview_url = None
                                target_caption = ""
                                image_urls = []
                                for item in post.items:
                                    if item.video_url:
                                        has_video = True
                                        target_video_url = item.video_url
                                        target_video_filename = item.video_filename
                                        target_preview_url = item.preview_image_url

                                    elif item.image_url:
                                        image_urls.append(item.image_url)

                                    for node in item.sidecars:
                                        if node.is_video and node.url:
                                            has_video = True
                                            target_video_url = node.url
                                            target_video_filename = node.filename
                                            target_preview_url = node.preview_image_url

                                        else:
                                            image_urls.append(node.url)
                                    if item.caption and item.caption.text:
                                        target_caption = item.caption.text

                                if not has_video and image_urls:
                                    embeds: list[discord.Embed] = []
                                    color_task = None

                                    for img_url in image_urls:
                                        e = post.make_embed()
                                        e.color = 1

                                        if not color_task:
                                            color_task = spawn_task(set_color(e, img_url, timeout_duration=1), self.active_tasks)
                                        else:
                                            spawn_task(set_color(e, img_url), self.active_tasks)
                                        await checkpoint()
                                        e.set_image(url=img_url)

                                        e.description = (
                                            f"[Instagram]({post.share_url}) requested by {message.author.mention}\n\n{post.clean_caption(target_caption)}"
                                        )

                                        if "threads" in url:
                                            e._footer["text"] = e._footer["text"].replace("Instagram", "Threads")
                                            e.description = e.description.replace("Instagram", "Threads")
                                            e._footer["text"] = e._footer["text"].replace("instagram", "Threads")

                                        embeds.append(e)

                                    if color_task:
                                        await color_task
                                    paginator = BotEmbedPaginator(ctx, embeds)
                                    self.bot.ioloop.spawn_callback(paginator.run)
                                else:
                                    embed = post.make_embed()
                                    if target_preview_url:
                                        _t = spawn_task(set_color(embed, target_preview_url, timeout_duration=1.2), self.active_tasks)
                                    else:
                                        _t = asyncio.sleep(0)

                                    if "threads" in url:
                                        _name = "Threads"
                                        embed.url = post.share_url
                                        embed.title = f"\n{post.clean_caption(target_caption)}"
                                        embed.description = ""

                                        likes = f"❤️  {intword(post.items[0].like_count)}" if post.items and post.items[0].like_count else ""
                                        cmnts = f"💬  {intword(post.items[0].reply_count)}" if post.items and post.items[0].reply_count else ""
                                        embed._footer["text"] = f"threads | {likes} {cmnts} | {message.author}"

                                    else:
                                        _name = "Instagram"
                                        embed.description = (
                                            f"[{_name}]({post.share_url}) requested by {message.author.mention}\n{post.clean_caption(target_caption)}"
                                        )
                                    await _t
                                    if "threads" in url:
                                        embed._footer["text"] = embed._footer["text"].replace("Instagram", "Threads")
                                        embed.description = embed.description.replace("Instagram", "Threads")

                                    if target_video_url:
                                        r = await curl.fetch(target_video_url)

                                        if len(r.body) > guild.filesize_limit:
                                            return await message.channel.send(content=target_video_url)
                                        await message.channel.send(embed=embed, file=discord.File(BytesIO(r.body), filename=target_video_filename))

                                    else:
                                        await message.channel.send(embed=embed)

                                if not message.mentions and not message.mention_everyone and not message.role_mentions:
                                    await message.delete(delay=0.1)

    @commands.command(hidden=True)
    @commands.cooldown(3, 300, commands.BucketType.user)
    async def highlights2(self, ctx: commands.Context, username: str, index: int = None):
        """Fetch a user's Instagram highlights."""
        async with ctx.typing(), asyncio.timeout(600):
            curl = get_curl()
            r = await curl.fetch(
                url_concat(f"https://dev.melaniebot.net/api/instagram/highlights/{username}", {"user_id": ctx.author.id}),
                headers=SHARED_API_HEADERS,
                raise_error=False,
            )

            if r.error:
                return await ctx.send(embed=make_e(f"{username} has no viewable highlights. Try later", 2))
            highlights = InstagramHighlightIndexResponse.parse_raw(r.body)

            if not highlights.highlights:
                return await ctx.send(embed=make_e(f"{username} has no higlights 🥺", 2), delete_after=10)
            highlights_root = AsyncPath("/home/melanie/highlights")
            await highlights_root.mkdir(exist_ok=True)
            user_root = AsyncPath(f"/home/melanie/highlights/{username}")
            # await
            await user_root.mkdir(exist_ok=True)
            async with asyncio.TaskGroup() as tg:
                sem = asyncio.Semaphore(2)

                async def set_highlights(hl_item: HighlightItem):
                    async with sem:
                        r = await curl.fetch(
                            CurlRequest(
                                url_concat(f"https://dev.melaniebot.net/api/instagram/highlight/{hl_item.id}", {"user_id": ctx.author.id}),
                                headers=SHARED_API_HEADERS,
                            ),
                        )
                    resp = InstagramHighlightResponse.parse_raw(r.body)

                    async def write_item(item):
                        url = item.url

                        async with self.bot.aio.get(url) as r:
                            if r.ok:
                                name = get_filename_from_url(url)
                                file = user_root / name
                                await file.unlink(missing_ok=True)
                                async with await file.open("wb") as f2:
                                    async for chunk, _ in r.content.iter_chunks():
                                        await f2.write(chunk)
                                log.success("Wrote {} @ {}", name, file)

                            else:
                                return log.error("Error {}", url)

                    for item in resp.items:
                        tg.create_task(write_item(item))

                if index:
                    index += 1
                    await set_highlights(highlights.highlights[index])
                else:
                    for i in highlights.highlights:
                        tg.create_task(set_highlights(i))

    @commands.command()
    @commands.cooldown(3, 45, commands.BucketType.user)
    async def highlights(self, ctx: commands.Context, username: str, index: int = None):
        """Fetch a user's Instagram highlights."""
        sem = asyncio.Semaphore(2)
        async with ctx.typing(), asyncio.timeout(60):
            curl = get_curl()
            args = {"user_id": ctx.author.id}
            r = await curl.fetch(
                url_concat(f"https://dev.melaniebot.net/api/instagram/highlights/{username}", args),
                headers=SHARED_API_HEADERS,
                raise_error=False,
            )

            if r.error:
                return await ctx.send(embed=make_e(f"{username} has no viewable highlights. Try later", 2))
            highlights = InstagramHighlightIndexResponse.parse_raw(r.body)
            urls = []

            if not highlights.highlights:
                return await ctx.send(embed=make_e(f"{username} has no higlights 🥺", 2), delete_after=10)

            async def set_highlights(hl_item: HighlightItem):
                args = {}
                async with sem:
                    _url = url_concat(f"https://dev.melaniebot.net/api/instagram/highlight/{hl_item.id}", args)
                    r = await curl.fetch(_url, headers=SHARED_API_HEADERS)
                    resp = InstagramHighlightResponse.parse_raw(r.body)
                    urls.extend([i.url async for i in aiter(resp.items)])

            if index:
                index += 1
                await set_highlights(highlights.highlights[index])
            else:
                await asyncio.gather(*[set_highlights(i) async for i in aiter(highlights.highlights)])
            urls2 = []
            total = len(urls)
            for idx, url in enumerate(urls, start=1):
                urls2.append(f"{idx}/{total} {url}")
            paginator = BotEmbedPaginator(ctx, urls2)
            self.bot.ioloop.add_callback(paginator.run)

            await ctx.send(embed=make_e(f"There are a total of {len(urls)} highlight media items loaded", status="info"), delete_after=10)

    @commands.command()
    @commands.cooldown(4, 300, commands.BucketType.user)
    async def igstory(self, ctx: commands.Context, username: str):
        """Fetch a user's Instagram story."""
        async with ctx.typing(), asyncio.timeout(60):
            url = get_api_baseurl("api", "instagram", "story", username)
            url = url_concat(url, {"user_id": ctx.author.id})
            curl = get_curl()
            r = await curl.fetch(CurlRequest(url, headers=SHARED_API_HEADERS), raise_error=False)

            if r.code == 404:
                return await ctx.send(embed=make_e(f"**{username}** has no active story", 2))
            if r.code >= 400:
                raise r.error
            stories = InstagramStoryResponse.parse_raw(r.body)
            if stories.author.is_private and not ctx.bot_owner:
                return await ctx.send(embed=make_e(f"**{username}** is private", 3))
            if stories.items:
                return await self.igstory_task(ctx, username, stories)

    async def igstory_task(self, ctx: commands.Context, username: str, stories: InstagramStoryResponse = None):
        """Fetch someone's current Instagram story anonymously!."""
        curl = get_curl()
        loading_msg = None
        log.warning(f"Request to load {username} story from {ctx.author} @ {ctx.guild}")

        try:
            if stories:
                loading_msg = None
            else:
                loading_msg = await ctx.send(embed=make_e(f"Fetching **{username}**'s latest Instagram story..", status="info"))
                url = get_api_baseurl("api", "instagram", "story", username)
                args = {}
                if ctx.bot_owner:
                    args["ctx"] = "pure"

                r = await curl.fetch(url_concat(url, args), raise_error=False)
                if r.code == 404:
                    return await ctx.send(embed=make_e(f"Username {username} does not exist", status=2))
                elif r.code == 422:
                    return await ctx.send(embed=make_e(f"Username {username} has no active stories", status=2))
                elif r.code < 400:
                    data = r.body
                    stories = InstagramStoryResponse.parse_raw(data)
                else:
                    raise ValueError(r.body)
        finally:
            if loading_msg:
                await loading_msg.delete(delay=2)

            for i in stories.items:
                i.url = i.url.replace("m.melaniebot.net", "cache.hurt.af")
            urls = [f"{idx}/{len(stories.items)} {i.url}" for idx, i in enumerate(stories.items, start=1)]
            paginator = BotEmbedPaginator(ctx, urls)
            self.bot.ioloop.add_callback(paginator.run)
