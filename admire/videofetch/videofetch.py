from __future__ import annotations

import asyncio
import io
import pickle
import re
from collections import defaultdict
from contextlib import ExitStack, suppress
from functools import partial
from re import Pattern
from typing import Optional

import discord
import tuuid
import yarl
from distributed import Event
from filetype import guess_mime
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from xxhash import xxh32_hexdigest

from melanie import (
    checkpoint,
    default_lock_cache,
    get_curl,
    get_redis,
    make_e,
    spawn_task,
    url_to_mime,
    x3hash,
)
from melanie.core import create_task
from melanie.curl import CurlError, S3Curl
from melanie.helpers import get_image_colors2
from runtimeopt import offloaded
from videofetch.auto_orientation import maybe_correct_orientation
from videofetch.core import VideoDownload
from videofetch.h264_conv import run_video_convpipeline
from videofetch.helpers import check_message, download_video, make_discord_result

IMAGE_LINKS: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg|webp|gif|webm|png|svg)(\?size=[0-9]*)?)", flags=re.I)


async def defer_typing(channel: discord.TextChannel, event: Event, lock: asyncio.Lock) -> None:
    while True:
        if not lock.locked():
            return
        if await event.wait("3s"):
            async with channel.typing():
                await asyncio.sleep(3)


class VideoFetch(commands.Cog):
    """Automatically fetch videos in chat."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.video_semaphore = asyncio.Semaphore(2)
        self.locks = default_lock_cache()
        self.active_tasks = []
        self.stack = ExitStack()

        self.guild_sems = defaultdict(partial(asyncio.Semaphore, 2))

    def cog_unload(self) -> None:
        self.stack.close()

    async def auto_orient_image(self, url: str) -> tuple[str, bytes]:
        key = xxh32_hexdigest(f"imgorient2:{xxh32_hexdigest(url)}")
        async with self.locks[key]:
            redis = get_redis()
            cached = await redis.get(key)
            if not cached:
                cached = await maybe_correct_orientation(url)
                if cached:
                    await redis.set(key, pickle.dumps(cached), ex=320)
                    return cached
            else:
                return pickle.loads(cached)

    @commands.command(hidden=True)
    @checks.is_owner()
    async def encode(self, ctx: commands.Context, url: str = None):
        from videofetch.h264_conv import make_h264_video

        make_h264_video = offloaded(make_h264_video)

        if not url:
            message: discord.Message = ctx.message
            if not message.attachments:
                return await ctx.send_help()
            url = str(message.attachments[0].url)
        async with ctx.typing(), asyncio.timeout(300):
            video = await make_h264_video(url, timeout=290)
            if len(video) > ctx.guild.filesize_limit:
                return await ctx.send(embed=make_e("File is too large to send!", 3))
            return await ctx.send(file=discord.File(io.BytesIO(video), filename=f"{tuuid.tuuid()}.mp4"))

    @commands.command(hidden=True)
    async def videodl(self, ctx: commands.Context):
        with log.catch(exclude=asyncio.CancelledError):
            return await self.video_fetch(message=ctx.message)

    async def video_fetch(self, message: discord.Message):
        guild: discord.Guild = message.guild
        color_task = None
        if message.author.bot or not message.guild:
            return
        content: str = message.content.strip()
        if "@" in content:
            return
        if content.startswith("bleed"):
            return
        sem = self.guild_sems[message.guild.id]
        if sem.locked():
            return log.warning("Semaphore is locked for guild {}", guild)
        async with sem:
            await checkpoint()
            curl = get_curl()
            channel: discord.TextChannel = message.channel
            spawn_task(self.check_for_images(message), self.active_tasks)
            url = check_message(message.content)
            if not url:
                return
            if await self.bot.redis.ratelimited(f"videofetchdl:{message.author.id}", 2, 6):
                return log.error("Videofetch ratelimited for {} {}", message.author, message.content)
            task_id = tuuid.tuuid()
            event = Event(name=task_id)
            key = f"videofetch:{x3hash(str(url))}"
            async with self.locks[key]:
                async with asyncio.timeout(80):
                    spawn_task(defer_typing(message.channel, event, self.locks[key]), self.active_tasks)
                    url = str(url)
                    log.info(f"Requesting to load {url} from {message.author} @ {message.guild}")
                    download = await download_video(url, task_id)

                    if download == "TIME":
                        return await message.add_reaction("🕒")
                    if not download:
                        return log.warning(f"No download for {url}")
                    create_task(event.set())

                    download: VideoDownload = VideoDownload.from_bytes(download)

                    if download.age_limit == 18 and not channel.is_nsfw():
                        return await message.add_reaction("🔞")
                result = await make_discord_result(download, message.author)
                if result:
                    if download.thumbnails:
                        thumb = download.thumbnails[0]
                        if thumb and thumb.url:

                            async def set_color(result):
                                lookup = await get_image_colors2(thumb.url)
                                if lookup:
                                    result.embed.color = lookup.dominant.decimal

                            color_task = spawn_task(set_color(result), self.active_tasks)
                            await checkpoint()

                    if result.file_size > guild.filesize_limit:
                        url = f"https://volatile.hurt.af/{result.file.filename}"
                        try:
                            await curl.fetch(url)
                        except CurlError:
                            await S3Curl.put_object("volatile", result.file.filename, download.video_bytes)
                        await message.channel.send(url)

                    else:
                        if color_task:
                            await color_task
                        await message.channel.send(embed=result.embed, file=result.file)
                    if not message.mentions and not message.reference and not message.role_mentions and not message.mention_everyone:
                        with suppress(discord.HTTPException):
                            await message.delete()

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        message.content = str(message.content).replace("https://x.com", "https://twitter.com")
        # if await self.bot.cog_disabled_in_guild_raw(self.qualified_name, message.guild.id):
        if message.attachments:
            mime, ext = url_to_mime(str(message.attachments[0].url))
            if mime and "video" in mime and await self.bot.allowed_by_whitelist_blacklist(message.author):
                return await self.run_video_compat(message)

        else:
            url = check_message(message.content)
            if url and await self.bot.allowed_by_whitelist_blacklist(message.author):
                ctx = await self.bot.get_context(message)
                ctx.command = self.bot.get_command("videodl")
                await self.bot.invoke(ctx)

    async def check_for_images(self, message: discord.Message) -> list[Optional[str]]:
        if not message.guild:
            return

        if message.guild.id != 915317604153962546:
            return
        rlkey = f"imgcheck:{message.author.id}"
        lock = self.locks[rlkey]
        redis = get_redis()
        if lock.locked():
            return

        async with lock:
            content = str(message.content)

            for attachment in message.attachments:
                content += f" {attachment.url}"

            if not message.embeds and not message.attachments:
                await asyncio.sleep(0.3)
            if not message.embeds and not message.attachments:
                return
            imgs = IMAGE_LINKS.findall(content)
            results = None

            if imgs:
                imgs = [i[0] for i in imgs]
                results = await asyncio.gather(*[self.auto_orient_image(i) for i in imgs])
                if any(results):
                    if await redis.ratelimited(rlkey, 2, 20):
                        await message.add_reaction("🕰️")
                        return log.warning("Rotate ratelimit {}", rlkey)
                    if len(results) > 1:
                        fixed_urls = []
                        async with asyncio.TaskGroup() as tg:
                            for filename, data in results:
                                _key = f"orient_{filename}"
                                tg.create_task(S3Curl.put_object("volatile", _key, data, guess_mime(data)))
                                fixed_urls.append(f"https://volatile.hurt.af/{data}")
                        content = "\n".join(fixed_urls)
                        content = f"Here are your attachments corrected: {content}"
                        return await message.channel.send(content)
                    else:
                        if results := results[0]:
                            filename = results[0]
                            data = results[1]
                            return await message.channel.send("Here is your file corrected!", file=discord.File(io.BytesIO(data), filename=filename))

    async def run_video_compat(self, message: discord.Message):
        curl = get_curl()
        channel: discord.TextChannel = message.channel

        _url = yarl.URL(str(message.attachments[0].url))
        url = str(_url)
        if _url.suffix == ".jfif":
            embed = make_e(
                f" **{message.author.mention}** That video likely isn't playable for the users in chat. I've already started converting it and will send soon.",
                status="info",
            )

            await channel.send(embed=embed, delete_after=3)
            r = await curl.fetch(url)
            return (await channel.send(f"{message.author.mention}, here is your video file converted", file=discord.File(r.buffer, filename="converted.gif")),)

        key = f"conv2_{xxh32_hexdigest(url)}"
        async with self.locks[key]:
            max_timeout = 120
            event = Event(key, client=self.bot.dask)
            remuxed = spawn_task(run_video_convpipeline(url, event, timeout=max_timeout), self.active_tasks)
            if not await event.wait(10):
                return await remuxed
            embed = make_e(
                f" **{message.author.mention}** That video likely isn't playable for the users in chat. I've already started converting it and will send soon.",
                status="info",
            )
            msg = await channel.send(embed=embed)
            try:
                async with channel.typing():
                    data = await remuxed
                    ident = f"melanieConverted{xxh32_hexdigest(data)}.mp4"
                try:
                    await message.reply(f"{message.author.mention}, here is your video file converted", file=discord.File(io.BytesIO(data), filename=ident))
                except discord.HTTPException:
                    await channel.send(f"{message.author.mention}, here is your video file converted", file=discord.File(io.BytesIO(data), filename=ident))
            finally:
                await msg.delete(delay=0.3)

                # await message.delete
