from __future__ import annotations

import asyncio
import io
import random
import sys
import typing
import urllib
import uuid
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union, cast

import discord
import discord.http
import PIL
import regex as re
import tuuid
import wand
import wand.color
import wand.drawing
from aiomisc import cancel_tasks
from loguru import logger as log
from melaniebot.cogs.alias.alias import current_alias
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.data_manager import bundled_data_path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein
from xxhash import xxh32_hexdigest
from yarl import URL

from colorme.colorme import ColorSearchResult, build_color_data
from melanie import (
    CurlRequest,
    aiter,
    cancel_tasks,
    capturetime,
    default_lock_cache,
    get_curl,
    get_dask,
    get_redis,
    make_e,
)
from melanie.core import spawn_task
from notsobot.converter import FuzzyMember, ImageFinder
from notsobot.helpers import (
    add_watermark,
    api_make_text2,
    bytes_download,
    do_ascii,
    do_glitch,
    do_vw,
    do_waaw,
    gen_bonk,
    gen_horny,
    gen_neko,
    gen_simp,
)
from notsobot.utils import (
    gif_mimes,
    image_mimes,
    make_beautiful_gif,
    make_beautiful_img,
    make_merge,
    make_trump_gif,
)
from runtimeopt.global_dask import get_dask

if TYPE_CHECKING:
    from _typeshed import SupportsRead


def make_mc(b: typing.Union[SupportsRead[bytes], bytes, Path, str], txt: typing.Union[bytes, str]) -> tuple[bytes, int]:
    image = Image.open(b).convert("RGBA")
    draw = ImageDraw.Draw(image)
    font_path = "/home/melanie/data/cogs/CogManager/cogs/notsobot/data/Minecraftia.ttf"
    font = ImageFont.truetype(font_path, 17)
    draw.text((60, 30), txt, (255, 255, 255), font=font)
    final = BytesIO()
    image.save(final, "png")
    data = final.getvalue()
    size = len(data)
    return data, size


class NotSoBot(commands.Cog):
    """Rewrite of many NotSoBot commands to work on melaniebot."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot

        self.font_path = "/home/melanie/data/cogs/CogManager/cogs/notsobot/data"
        self.color_table: dict = None
        self.datapath = bundled_data_path(self)
        self.obama_sem = asyncio.Semaphore(2)
        self.locks = default_lock_cache()
        self.color_table_fast = None
        self._cd = commands.CooldownMapping.from_cooldown(3, 3.0, commands.BucketType.member)  # Change accordingly
        self.active_tasks = []
        spawn_task(self.init(), self.active_tasks)

    def cog_unload(self) -> None:
        cancel_tasks(self.active_tasks)

    async def init(self):
        with capturetime("global colors"):
            self.color_table = await build_color_data(keys_are_hex=True)
            self.color_table_fast = await build_color_data(keys_are_hex=False, strip_space=True)

    def get_ratelimit(self, message: discord.Message) -> typing.Optional[int]:
        """Returns the ratelimit left."""
        bucket = self._cd.get_bucket(message)
        return bucket.update_rate_limit()

    def random(self, image: bool = False, ext: str = "png") -> str:
        h = str(uuid.uuid4().hex)
        return f"{h}.{ext}" if image else h

    @property
    def dask(self):
        return get_dask()

    async def safe_send(self, ctx: commands.Context, text: Optional[str], file: discord.File, file_size: int) -> None:
        if not ctx.channel.permissions_for(ctx.me).send_messages:
            file.close()
            return
        if not ctx.channel.permissions_for(ctx.me).attach_files:
            await ctx.send("I don't have permission to attach files.")
            file.close()
            return
        BASE_FILESIZE_LIMIT = 8 * 1024 * 1024
        if ctx.guild and file_size < ctx.guild.filesize_limit:
            await ctx.send(content=text, file=file)
        elif not ctx.guild and file_size < BASE_FILESIZE_LIMIT:
            await ctx.send(content=text, file=file)
        else:
            await ctx.send("The contents of this command is too large to upload!")
        file.close()

    async def dl_image(self, url: Union[discord.Asset, discord.Attachment, str]) -> Optional[BytesIO]:
        curl = get_curl()
        r = await curl.fetch(str(url))
        return r.buffer

    @commands.command()
    @commands.cooldown(2, 10, commands.BucketType.guild)
    async def obama(self, ctx: commands.Context, *, text: str) -> None:
        """Synthesize video clips of Obama."""
        text = ctx.message.clean_content[len(f"{ctx.prefix}{ctx.invoked_with}") :]
        if len(text) > 280:
            msg = "A maximum character total of 280 is enforced. You sent: `{}` characters"
            return await ctx.send(msg.format(len(text)))

        text = " ".join(text.split()).lower()
        get_redis()

        cache_key = f"melanieObama{xxh32_hexdigest(text)}"

        if self.obama_sem.locked():
            return await ctx.send(embed=make_e("I'm working on other videos at the moment. Try again in a few seconds."))

        async with self.obama_sem, self.locks[cache_key]:
            cached = await self.bot.redis.get(cache_key)
            if not cached:
                curl = get_curl()
                payload = urllib.parse.urlencode({"input_text": text})
                async with ctx.typing():

                    async def _fetch_file():
                        r = await curl.fetch(
                            CurlRequest("http://talkobamato.me/synthesize.py", body=payload, method="POST", headers=None, follow_redirects=True),
                        )
                        _url = URL(r.effective_url)
                        _key = _url.query["speech_key"]
                        target = f"http://talkobamato.me/synth/output/{_key}/obama.mp4"
                        await asyncio.sleep(4)
                        async with asyncio.timeout(30):
                            while True:
                                r = await curl.fetch(target, raise_error=False)
                                if r.code < 400:
                                    cached = r.body
                                    if len(cached) < 1000:
                                        raise ValueError("smallpayload")
                                    return cached
                                else:
                                    await asyncio.sleep(4)
                                    continue

                    cached = await _fetch_file()
                    await self.bot.redis.set(cache_key, cached, ex=900)

            return await ctx.send(file=discord.File(BytesIO(cached), filename=f"{cache_key}.mp4"))

    @commands.command()
    async def beautiful(self, ctx: commands.Context, user: discord.Member = None, is_gif: bool = False) -> None:
        """Generate a beautiful image using users avatar.

        `user` the user whos avatar will be places on the image `is_gif`
        True/False to create a gif if the user has a gif avatar

        """
        if user is None:
            user = ctx.message.author
        async with ctx.channel.typing():
            file, file_size = await self.make_beautiful(user, is_gif)
            if file is None:
                await ctx.send("sorry something went wrong!")
                return
        await self.safe_send(ctx, None, file, file_size)

    @commands.command(aliases=["isnowillegal"])
    async def trump(self, ctx: commands.Context, *, message) -> None:
        """Generate isnowillegal gif image.

        `message` will be what is pasted on the gif

        """
        async with ctx.channel.typing():
            task = self.dask.submit(make_trump_gif, text=message, priority=5)

            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except TimeoutError:
                return
        await self.safe_send(ctx, None, discord.File(io.BytesIO(file), "trump.gif"), file_size)

    @commands.command(aliases=["expand"])
    @commands.cooldown(1, 5)
    async def ascii(self, ctx, *, text: str):
        """Convert text into ASCII."""
        if len(text) > 1000:
            await ctx.send("Text is too long!")
            return
        if text in {"donger", "dong"}:
            text = "8====D"
        async with ctx.typing():
            task = self.dask.submit(do_ascii, text)
            try:
                file, txt, file_size = await asyncio.wait_for(task, timeout=60)
            except TimeoutError:
                return await ctx.send("That image is too large.")
            if file is False:
                await ctx.send(":no_entry: go away with your invalid characters.")
                return
            msg = None if len(txt) >= 1999 or len(txt) > 600 else f"```fix\n{txt}```"
            await self.safe_send(ctx, msg, file, file_size)

    async def make_beautiful(self, user: discord.User, is_gif: bool) -> tuple[Optional[discord.File], int]:
        template_str = "https://i.imgur.com/kzE9XBE.png"
        template = Image.open(await self.dl_image(template_str))
        if user.is_avatar_animated() and is_gif:
            avatar = Image.open(await self.dl_image(str(user.avatar_url_as(format="gif", size=128))))
            task = self.dask.submit(make_beautiful_gif, template=template, avatar=avatar, priority=5)

        else:
            avatar = Image.open(await self.dl_image(str(user.avatar_url_as(format="png", size=128))))
            task = self.dask.submit(make_beautiful_img, template=template, avatar=avatar, priority=5)

        try:
            temp: BytesIO = await asyncio.wait_for(task, timeout=60)
        except TimeoutError:
            avatar.close()
            template.close()
            return None, 0
        avatar.close()
        template.close()
        temp.seek(0)
        filename = "beautiful.gif" if is_gif else "beautiful.png"
        file = discord.File(temp, filename=filename)
        file_size = temp.tell()
        temp.close()
        return file, file_size

    async def truncate(self, channel, msg: typing.Sized) -> None:
        if len(msg) == 0:
            return
        split = [msg[i : i + 1999] for i in range(0, len(msg), 1999)]
        try:
            for s in split:
                await channel.send(s)
                await asyncio.sleep(0.21)
        except Exception as e:
            await channel.send(e)

    @commands.command()
    async def caption(self, ctx, urls: Optional[ImageFinder] = None, text: str = "Caption", color: str = "melanie", size: int = 60, x: int = 0, y: int = 0):
        """Add caption to an image.

        `[urls]` are the image urls or users or previous images in chat
        to add a caption to. `[text=Caption]` is the text to caption on
        the image. `[color=white]` is the color of the text. `[size=40]`
        is the size of the text `[x=0]` is the height the text starts at
        between 0 and 100% where 0 is the top and 100 is the bottom of
        the image. `[y=0]` is the width the text starts at between 0 and
        100% where 0 is the left and 100 is the right of the image.

        """
        with log.catch(reraise=True):
            if urls is None:
                urls = await ImageFinder().search_for_images(ctx)
            url = urls[0]
            if url is None:
                await ctx.send("Error: Invalid Syntax\n`.caption <image_url> <text>** <color>* <size>* <x>* <y>*`\n`* = Optional`\n`** = Wrap text in quotes`")
                return
            async with ctx.typing():
                xx = await ctx.message.channel.send("ok, processing")
                b, mime = await bytes_download(url)
                if mime not in image_mimes and not isinstance(url, discord.Asset):
                    return await ctx.send("That is not a valid image!")
                if b is False:
                    await ctx.send(":warning: **Command download function failed...**")
                    return
                is_gif = mime in gif_mimes
                font_path = f"{self.font_path}/arial.ttf"
                try:
                    color_str = color
                    color = wand.color.Color(color)
                except ValueError:
                    await ctx.send(":warning: **That is not a valid color!**")
                    await xx.delete()
                    return

                x = min(x, 100)
                x = max(x, 0)
                y = min(y, 100)
                y = max(y, 0)

                def make_caption_image(b, text, color, x, y, is_gif):
                    color = wand.color.Color(color)
                    from loguru import logger as log

                    font = wand.font.Font(path=font_path, size=size, color=color)
                    with log.catch(reraise=True):
                        final = BytesIO()
                        with wand.image.Image(file=b) as img:
                            i = img.clone()
                            x = int(i.height * (x * 0.01))
                            y = int(i.width * (y * 0.01))
                            if not is_gif:
                                i.caption(str(text), left=x, top=y, font=font)
                            else:
                                with wand.image.Image() as new_image:
                                    for frame in img.sequence:
                                        frame.caption(str(text), left=x, top=y, font=font)
                                        new_image.sequence.append(frame)
                                    new_image.save(file=final)
                            i.save(file=final)
                        file_size = final.tell()
                        final.seek(0)
                        filename = f"caption.{'gif' if is_gif else 'png'}"
                        file = discord.File(final, filename=filename)
                        final.close()
                        return file, file_size

                await xx.delete()

                task = self.dask.submit(make_caption_image, b, text, color_str, x, y, is_gif)
                try:
                    file, file_size = await asyncio.wait_for(task, timeout=60)
                except TimeoutError:
                    return await ctx.send("That image is too large.")
                await ctx.send(file=file)

    def trigger_image(self, path: BytesIO, t_path: BytesIO) -> tuple[discord.File, int]:
        final = BytesIO()
        with wand.image.Image(width=512, height=680) as img:
            img.format = "gif"
            img.dispose = "background"
            img.type = "optimize"
            with wand.image.Image(file=path) as top_img:
                top_img.transform(resize="640x640!")
                with wand.image.Image(file=t_path) as trigger:
                    with wand.image.Image(width=512, height=660) as temp_img:
                        i = top_img.clone()
                        t = trigger.clone()
                        temp_img.composite(i, -60, -60)
                        temp_img.composite(t, 0, 572)
                        img.composite(temp_img)
                    with wand.image.Image(width=512, height=660) as temp_img:
                        i = top_img.clone()
                        t = trigger.clone()
                        temp_img.composite(i, -45, -50)
                        temp_img.composite(t, 0, 572)
                        img.sequence.append(temp_img)
                    with wand.image.Image(width=512, height=660) as temp_img:
                        i = top_img.clone()
                        t = trigger.clone()
                        temp_img.composite(i, -50, -45)
                        temp_img.composite(t, 0, 572)
                        img.sequence.append(temp_img)
                    with wand.image.Image(width=512, height=660) as temp_img:
                        i = top_img.clone()
                        t = trigger.clone()
                        temp_img.composite(i, -45, -65)
                        temp_img.composite(t, 0, 572)
                        img.sequence.append(temp_img)
            for frame in img.sequence:
                frame.delay = 2
            img.save(file=final)
        file_size = final.tell()
        final.seek(0)
        file = discord.File(final, filename="triggered.gif")
        final.close()
        return file, file_size

    @commands.command()
    @commands.cooldown(1, 5)
    async def triggered(self, ctx, urls: ImageFinder = None):
        """Generate a Triggered Gif for a User or Image."""
        if urls is None:
            urls = [ctx.author.avatar_url_as(format="png")]
        avatar = urls[0]
        async with ctx.typing():
            img, mime = await bytes_download(str(avatar))
            trig, mime = await bytes_download("https://i.imgur.com/zDAY2yo.jpg")
            if img is False or trig is False:
                await ctx.send(":warning: **Command download function failed...**")
                return
            try:
                task = self.dask.submit(self.trigger_image, img, trig)
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except TimeoutError:
                return await ctx.send("Error creating trigger image")
            await self.safe_send(ctx, None, file, file_size)

    @commands.command(aliases=["aes"])
    async def aesthetics(self, ctx, *, text: str) -> None:
        """Returns inputed text in aesthetics."""
        final = ""
        pre = " ".join(text)
        for char in pre:
            if ord(char) not in range(33, 127):
                final += char
                continue
            final += chr(ord(char) + 65248)
        await self.truncate(ctx.message.channel, final)

    @commands.command()
    @commands.cooldown(1, 5)
    async def merge(self, ctx, vertical: Optional[bool] = True, *, urls: Optional[ImageFinder]):
        """Merge/Combine Two Photos.

        `[vertical=True]` `true` or `false` to merge vertically.
        `[urls]` The Image URL's you want to merge together. If not
        supplied images are searched from message history.

        """
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        if not urls:
            return await ctx.send("No images found.")
        async with ctx.typing():
            if len(urls) == 1:
                await ctx.send("You need to supply more than 1 image.")
                return
            xx = await ctx.message.channel.send("ok, processing")
            count = 0
            list_im = []
            for url in urls:
                log.debug(url)
                count += 1
                b, mime = await bytes_download(str(url))
                if sys.getsizeof(b) == 215:
                    await ctx.send(f":no_entry: Image `{count}` is invalid!")
                    continue
                if not b:
                    continue
                list_im.append(b)

            if len(list_im) < 2:
                return await ctx.send("You need to supply more than 1 image.")
            await xx.delete()
            task = self.dask.submit(make_merge, list_im, vertical)
            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, PIL.UnidentifiedImageError):
                return await ctx.send("That image is either too large or image filetype is unsupported.")
            await self.safe_send(ctx, None, file, file_size)

    async def get_colour(self, channel):
        try:
            if await self.bot.db.guild(channel.guild).use_bot_color():
                return channel.guild.me.colour
            else:
                return await self.bot.db.color()
        except AttributeError:
            return await self.bot.get_embed_colour(channel)

    async def first_word(self, msg: str) -> str:
        return msg.split(" ")[0].lower()

    async def get_prefix(self, message: discord.Message) -> str:
        """From melaniebot Alias Cog Tries to determine what prefix is used in a
        message object. Looks to identify from longest prefix to smallest. Will
        raise ValueError if no prefix is found.

        :param message: Message object
        :return:

        """
        try:
            guild = message.guild
        except AttributeError:
            guild = None
        content = message.content
        try:
            prefixes = await self.bot.get_valid_prefixes(guild)
        except AttributeError:
            # Melanie 3.1 support
            prefix_list = await self.bot.command_prefix(self.bot, message)
            prefixes = sorted(prefix_list, key=lambda pfx: len(pfx), reverse=True)

        if (fun_cog := self.bot.get_cog("Fun")) and (custom_prefix := fun_cog.prefix_cache.get(message.author.id)):
            prefixes.extend(custom_prefix)
        for p in prefixes:
            if content.startswith(p):
                return p
        msg = "No prefix found."
        raise ValueError(msg)

    async def _do_color_search(self, query: str):
        final = []
        fuzzer = process.extract_iter(query.lower(), self.color_table, scorer=DamerauLevenshtein.normalized_distance)
        async for res in aiter(fuzzer, steps=200):
            if res[1] == 0:
                final.insert(0, res)
                break
            if res[1] < 1.0:
                final.append(res)

        return [ColorSearchResult(name=x[0], score=x[1], code=x[2]) for x in sorted(final, key=lambda x: x[1])]

    async def search(self, query) -> list[ColorSearchResult]:
        results: list[ColorSearchResult] = await self._do_color_search(query)
        return results

    async def color_converter(self, hex_code_or_color_word: str):
        if hex_code_or_color_word == "black":
            hex_code_or_color_word = "010101"

        if hex_match := re.match(r"#?[a-f0-9]{6}", hex_code_or_color_word.lower()):
            return f"0x{hex_code_or_color_word.lstrip('#')}"
        search = await self.search(hex_code_or_color_word)
        best_match = search[0]
        return best_match.code

    @commands.command()
    async def tti(self, ctx: commands.Context, color: str, *, txt: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Generate an image of text."""
        ctx.channel: discord.TextChannel = ctx.channel
        color = await self.color_converter(color)
        cached = await api_make_text2(color=color, txt=txt)
        return await ctx.send(file=discord.File(BytesIO(cached), f"melTxt{tuuid.tuuid()}.webp"))

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.color_table_fast or not self.color_table:
            return
        if len(message.content) < 2 or message.guild is None:
            return
        if current_alias.get():
            return
        guild = message.guild
        channel: discord.TextChannel = message.channel
        if not isinstance(message.author, discord.Member):
            return
        try:
            prefix = await self.get_prefix(message)
        except ValueError:
            return
        txt = message.clean_content
        alias = await self.first_word(txt[len(prefix) :])
        alias = alias.strip()
        if not alias:
            return
        removals = len(alias) + len(prefix)
        requested = txt[removals:]
        if not requested:
            return
        color_match = self.color_table_fast.get(alias)
        if not color_match:
            return
        if not await self.bot.allowed_by_whitelist_blacklist(message.author):
            return
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        cached = None
        conv = commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)
        ctx = await self.bot.get_context(message)
        requested = await conv.convert(ctx, requested)
        key = f"melanieText:{xxh32_hexdigest(f'{color_match}:{requested}')}.webp"
        cached = await api_make_text2(color=color_match, txt=requested)
        if cached:
            await channel.send(file=discord.File(BytesIO(cached), key))
        await self

    @commands.command(aliases=["vaporwave", "vape", "vapewave"])
    @commands.cooldown(2, 5)
    async def vw(self, ctx, urls: ImageFinder = None, *, txt: str = None):
        """Add vaporwave flavours to an image."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        if txt is None:
            txt = "vapor wave"
        b, mime = await bytes_download(url)
        if b is False:
            await ctx.send(":warning: **Command download function failed...**")
            return
        try:
            task = self.dask.submit(do_vw, b, txt)
            file, file_size = await asyncio.wait_for(task, timeout=60)
        except TimeoutError:
            return await ctx.send("That image is too large.")
        except Exception:
            return await ctx.send("That image cannot be vaporwaved.")
        await self.safe_send(ctx, None, file, file_size)

    @commands.command(aliases=["achievement"])
    async def minecraftachievement(self, ctx, *, txt: str):
        """Generate a Minecraft Achievement."""
        b, mime = await bytes_download("https://i.imgur.com/JtNJFZy.png")
        if b is False:
            await ctx.send(":warning: **Command download function failed...**")
            return
        if len(txt) > 20:
            txt = f"{txt[:20]} ..."

        try:
            task = self.dask.submit(make_mc, b, txt)

            file, file_size = await asyncio.wait_for(task, timeout=60)
        except TimeoutError:
            return await ctx.send("That image is too large.")
        except Exception:
            return await ctx.send("I cannot make that minecraft achievement.")
        await self.safe_send(ctx, None, discord.File(io.BytesIO(file), "mc.png"), file_size)

    @commands.command(aliases=["wm"])
    async def watermark(self, ctx, urls: ImageFinder = None, mark: str = None, x: int = 0, y: int = 0, transparency: Union[int, float] = 0):
        """Add a watermark to an image.

        `[urls]` are the image urls or users or previous images in chat
        to add a watermark to. `[mark]` is the image to use as the
        watermark. By default the brazzers icon is used. `[x=0]` is the
        height the watermark will be at between 0 and 100% where 0 is
        the top and 100 is the bottom of the image. `[y=0]` is the width
        the watermark will be at between 0 and 100% where 0 is the left
        and 100 is the right of the image. `[transparency=0]` is a value
        from 0 to 100 which determines the percentage the watermark will
        be transparent.

        """
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            x = min(x, 100)
            x = max(x, 0)
            y = min(y, 100)
            y = max(y, 0)
            if transparency > 1 and transparency < 100:
                transparency = transparency * 0.01
            transparency = max(transparency, 0)
            if transparency > 100:
                transparency = 1
            b, mime = await bytes_download(url)
            if mime not in image_mimes + gif_mimes and not isinstance(url, discord.Asset):
                return await ctx.send("That is not a valid image.")
            if mark == "brazzers" or mark is None:
                wmm, mime = await bytes_download("https://i.imgur.com/YAb1RMZ.png")
                if wmm is False or b is False:
                    await ctx.send(":warning: **Command download function failed...**")
                    return
                wmm.name = "watermark.png"
                wm_gif = False
            else:
                wmm, mime = await bytes_download(mark)
                wm_gif = mime in gif_mimes
                if wmm is False or b is False:
                    await ctx.send(":warning: **Command download function failed...**")
                    return
                wmm.name = "watermark.gif" if wm_gif else "watermark.png"
            try:
                task = self.dask.submit(add_watermark, b, wmm, x, y, transparency, wm_gif)

                data, format = await asyncio.wait_for(task, timeout=120)
            except TimeoutError:
                return await ctx.send("That image is too large.")

            file = discord.File(io.BytesIO(data), filename=f"watermark.{format}")
            return await ctx.send(file=file)

    @commands.command(aliases=["jpglitch"])
    @commands.cooldown(2, 5)
    async def glitch(self, ctx, urls: ImageFinder = None, iterations: int = None, amount: int = None, seed: int = None):
        """Glitch a gif or png."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            if iterations is None:
                iterations = random.randint(1, 30)
            if amount is None:
                amount = random.randint(1, 20)
            elif amount > 99:
                amount = 99
            if seed is None:
                seed = random.randint(1, 20)
            b, mime = await bytes_download(url)
            mime in gif_mimes
            if b is False:
                await ctx.send(":warning: **Command download function failed...**")
                return

            task = self.dask.submit(do_glitch, b, amount, seed, iterations)
            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, PIL.UnidentifiedImageError):
                return await ctx.send("The image is either too large or image filetype is unsupported.")

            msg = f"Iterations: `{iterations}` | Amount: `{amount}` | Seed: `{seed}`"
            await self.safe_send(ctx, msg, file, file_size)

    # Thanks to Iguniisu#9746 for the idea
    @commands.command(aliases=["magik3", "mirror"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def waaw(self, ctx, urls: ImageFinder = None):
        """Mirror an image vertically right to left."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            b, mime = await bytes_download(url)
            if b is False:
                await ctx.send(":warning: **Command download function failed...**")
                return
            task = self.dask.submit(do_waaw, b)
            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, wand.exceptions.MissingDelegateError):
                return await ctx.send("The image is either too large or you're missing delegates for this image format.")
            await self.safe_send(ctx, None, file, file_size)

    @commands.command()
    async def flipimg(self, ctx, urls: ImageFinder = None):
        """Rotate an image 180 degrees."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            b, mime = await bytes_download(url)
            if b is False:
                await ctx.send(":warning: **Command download function failed...**")
                return

            def flip_img(b):
                with Image.open(b) as img:
                    img = ImageOps.flip(img)
                with BytesIO() as final:
                    img.save(final, "png")
                    file_size = final.tell()
                    final.seek(0)
                    file = discord.File(final, filename="flip.png")
                return file, file_size

            task = self.dask.submit(flip_img, b)
            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, PIL.UnidentifiedImageError):
                return await ctx.send("The image is either too large or image filetype is unsupported.")
            await self.safe_send(ctx, None, file, file_size)

    @commands.command()
    async def flop(self, ctx, urls: ImageFinder = None):
        """Flip an image."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            b, mime = await bytes_download(url)
            if mime not in image_mimes and not isinstance(url, discord.Asset):
                return await ctx.send("That is not a valid image!")
            if b is False:
                await ctx.send(":warning: **Command download function failed...**")
                return

            def flop_img(b):
                with Image.open(b) as img:
                    img = ImageOps.mirror(img)
                with BytesIO() as final:
                    img.save(final, "png")
                    file_size = final.tell()
                    final.seek(0)
                    file = discord.File(final, filename="flop.png")
                return file, file_size

            task = self.dask.submit(flop_img, b)

            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except TimeoutError:
                return await ctx.send("That image is too large.")
            await self.safe_send(ctx, None, file, file_size)

    @commands.command(aliases=["inverse", "negate"])
    async def invert(self, ctx, urls: ImageFinder = None):
        """Invert the colours of an image."""
        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            b, mime = await bytes_download(url)
            if b is False:
                await ctx.send(":warning: **Command download function failed...**")
                return

            def invert_img(b):
                with Image.open(b).convert("RGB") as img:
                    img = ImageOps.invert(img)
                with BytesIO() as final:
                    img.save(final, "png")
                    file_size = final.tell()
                    final.seek(0)
                    file = discord.File(final, filename="flop.png")
                return file, file_size

            task = self.dask.submit(invert_img, b)
            try:
                file, file_size = await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, PIL.UnidentifiedImageError):
                return await ctx.send("That image is either too large or image filetype is unsupported.")
            await self.safe_send(ctx, None, file, file_size)

    async def check_ignored_channel(self, message: discord.Message) -> bool:
        """https://github.com/Cog-Creators/Melanie-
        DiscordBot/blob/V3/release/3.0.0/melaniebot/cogs/mod/mod.py#L1273.
        """
        ctx = await self.bot.get_context(message)
        return await self.bot.ignored_channel_or_guild(ctx)

    async def local_perms(self, message: discord.Message) -> bool:
        """Check the user is/isn't locally whitelisted/blacklisted."""
        if await self.bot.is_owner(message.author):
            return True
        elif message.guild is None:
            return True
        author = cast(discord.Member, message.author)
        try:
            return await self.bot.allowed_by_whitelist_blacklist(
                message.author,
                who_id=message.author.id,
                guild_id=message.guild.id,
                role_ids=[r.id for r in author.roles],
            )
        except AttributeError:
            guild_settings = self.bot.db.guild(message.guild)
            local_blacklist = await guild_settings.blacklist()
            local_whitelist = await guild_settings.whitelist()

            _ids = [r.id for r in author.roles if not r.is_default()]
            _ids.append(message.author.id)
            if local_whitelist:
                return any(i in local_whitelist for i in _ids)

            return all(i not in local_blacklist for i in _ids)

    async def global_perms(self, message: discord.Message) -> bool:
        """Check the user is/isn't globally whitelisted/blacklisted."""
        if await self.bot.is_owner(message.author):
            return True
        try:
            return await self.bot.allowed_by_whitelist_blacklist(message.author)
        except AttributeError:
            whitelist = await self.bot.db.whitelist()
            if whitelist:
                return message.author.id in whitelist

            return message.author.id not in await self.bot.db.blacklist()

    @commands.command()
    async def rotate(self, ctx, degrees: int = 90, urls: ImageFinder = None):
        """Rotate image X degrees."""
        from wand.image import Image as ImageWand

        if urls is None:
            urls = await ImageFinder().search_for_images(ctx)
        url = urls[0]
        async with ctx.typing():
            b, mime = await bytes_download(url)
            if not b:
                return await ctx.send("That's not a valid image to rotate.")

            def rotate_img(b=b, degrees=degrees):
                with ImageWand(blob=b) as img:
                    format = "gif" if img.animation else "png"
                    img.rotate(int(degrees))

                    data = BytesIO(img.make_blob(format=format))
                    file = discord.File(data, filename=f"rotate.{format}")
                    file_size = sys.getsizeof(data)
                return file, file_size

            task = self.dask.submit(rotate_img, b=b, degrees=degrees, priority=5)
            try:
                file, file_size = await task
            except TimeoutError:
                return await ctx.send("That image is eiter too large or image filetype is unsupported.")
            await self.safe_send(ctx, f"Rotated: `{degrees}°`", file, file_size)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(aliases=["catgirl"], cooldown_after_parsing=True)
    async def neko(self, ctx, *, member: FuzzyMember = None) -> None:
        """Make a neko avatar..."""
        if not member:
            member = ctx.author

        async with ctx.typing():
            avatar = await self.get_avatar(member)
            task = self.dask.submit(gen_neko, avatar)
            image = await task
        if isinstance(image, str):
            await ctx.send(image)
        else:
            await ctx.send(file=image)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(cooldown_after_parsing=True)
    async def bonk(self, ctx: commands.Context, *, member: FuzzyMember = None) -> None:
        """Bonk!.

        Go to horny jail.

        """
        async with ctx.typing():
            bonker = False
            if member:
                bonker = ctx.author
            else:
                member = ctx.author

            async with ctx.typing():
                victim_avatar = await self.get_avatar(member)
                if bonker:
                    bonker_avatar = await self.get_avatar(bonker)
                    task = self.dask.submit(gen_bonk, victim_avatar, bonker_avatar)
                else:
                    task = self.dask.submit(gen_bonk, victim_avatar)
                image = await task
            if isinstance(image, str):
                await ctx.send(image)
            else:
                await ctx.send(file=image)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(cooldown_after_parsing=True)
    async def simp(self, ctx, *, member: FuzzyMember = None) -> None:
        """You are now a simp."""
        if not member:
            member = ctx.author
        async with ctx.typing():
            avatar = await self.get_avatar(member)
            task = self.dask.submit(gen_simp, avatar)
            image = await task
        if isinstance(image, str):
            await ctx.send(image)
        else:
            await ctx.send(file=image)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(cooldown_after_parsing=True)
    async def horny(self, ctx, *, member: FuzzyMember = None) -> None:
        """Assign someone a horny license."""
        member = member or ctx.author
        async with ctx.typing():
            avatar = await self.get_avatar(member)
            task = self.dask.submit(gen_horny, avatar)
            image = await task
        if isinstance(image, str):
            await ctx.send(image)
        else:
            await ctx.send(file=image)

    async def get_avatar(self, member: discord.User) -> io.BytesIO:
        avatar = BytesIO()
        await member.avatar_url.save(avatar, seek_begin=True)
        return avatar
