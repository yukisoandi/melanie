import asyncio
import io
import os
import random
import string
import time
from contextlib import asynccontextmanager, suppress
from typing import List, Optional, cast

import anyio
import discord
import yarl
from aiobotocore.session import get_session
from anyio import Path as AsyncPath
from filetype import guess_extension, guess_mime
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.data_manager import cog_data_path
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from types_aiobotocore_s3.client import S3Client

from melanie import (
    BaseModel,
    CurlAsyncHTTPClient,
    alru_cache,
    borrow_temp_file,
    get_curl,
    get_filename_from_url,
    log,
    make_e,
)
from melanie.core import spawn_task
from melanie.timing import capturetime
from notsobot.converter import ImageFinder


class FilesizeLimitExceeded(Exception):
    pass


class Image(BaseModel):
    command_name: str
    file_loc: str
    count: int
    author: int
    created_at: float
    author_name: str


class GuildSettings(BaseModel):
    images: List[Image]


class GlobalSettings(BaseModel):
    images: List[Image]


async def optimize_target(data: bytes, extension: str):
    opti_path = os.environ["OPTI_PATH"]
    async with borrow_temp_file(extension=extension) as tmpfile:
        await tmpfile.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(opti_path, *[str(tmpfile)], stdout=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(60):
                await proc.communicate()
            return await tmpfile.read_bytes()
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            raise


class Savepic(commands.Cog):
    """Store images as commands!."""

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot

        self.config = Config.get_conf(self, 2502, force_registration=True)
        self.config.register_global(images=[])
        self.config.register_guild(images=[])
        self.session = get_session()
        self.active_tasks = []
        self.download_sem = asyncio.BoundedSemaphore(24)
        self.root = AsyncPath(str(cog_data_path(self).absolute()))
        spawn_task(self.ensure_mime(), self.active_tasks)
        spawn_task(self.preload_globals(), self.active_tasks)

    @alru_cache
    async def download_file(self, loc: str):
        async with self.download_sem:
            loc = loc.removeprefix("/")
            url = yarl.URL(f"https://imgshare.hurt.af/{loc}")
            curl = get_curl()
            r = await curl.fetch(str(url))
            return r.body

    async def preload_globals(self):
        await self.bot.waits_uptime_for(30)
        with capturetime("global preloads"):
            _images = []
            _names = []

            async with anyio.create_task_group() as tg, self.get_s3() as s3:
                images = await self.config.images()

                for _img in images:
                    _img = Image.parse_obj(_img)
                    _names.append(_img.file_loc)

                _folder = self.root / "global"
                async for item in _folder.iterdir():
                    if item.name not in _names:
                        # _images.append(
                        #     Image(
                        #     ).dict()

                        log.warning("Item {} is not in configuration. Deleting the file and web image", item)
                        await item.unlink(missing_ok=True)
                        await s3.delete_object(Bucket="imgshare", Key=f"global/{item.name}")

                async def check_mime(img):
                    img = Image.parse_obj(img)
                    loc = f"/global/{img.file_loc}"
                    _path = AsyncPath(img.file_loc)
                    data = await self.download_file(loc)
                    ext = "." + guess_extension(data)
                    _mime = guess_mime(data)
                    if ext != _path.suffix:
                        _path2 = _path.with_suffix(ext)
                        img.file_loc = img.file_loc.replace(_path.suffix, _path2.suffix)
                        loc2 = f"/global/{img.file_loc}"
                        log.warning("{} is invalid. Renamed to {}", _path, _path2)
                        async with self.get_s3() as s3:
                            await s3.delete_object(Bucket="imgshare", Key=loc)
                            await s3.put_object(Bucket="imgshare", Key=loc2, Body=data, ContentType=_mime)
                        await self.download_file(loc2)
                    _images.append(img.dict())

                for img in images:
                    tg.start_soon(check_mime, img)

            await self.config.images.set(_images)

    async def ensure_mime(self):
        await self.bot.waits_uptime_for(30)
        with capturetime("savepic ensure mimes"):
            all_guilds = await self.config.all_guilds()

            async def check_guild_imgs(gid):
                _images = []
                _names = []
                settings = None
                async with self.config.guild_from_id(gid).all() as __data:
                    settings = GuildSettings.parse_obj(__data)
                    for _img in settings.images:
                        _img = Image.parse_obj(_img)
                        _names.append(_img.file_loc)
                    _folder = self.root / str(gid)
                    with suppress(FileNotFoundError):

                        async def delete_item(item):
                            async with self.get_s3() as s3:
                                await s3.delete_object(Bucket="imgshare", Key=f"{str(gid)}/{item.name}")
                            await item.unlink(missing_ok=True)

                        async for item in _folder.iterdir():
                            if item.name not in _names:
                                await delete_item(item)

                    async def check_img(img):
                        try:
                            loc = f"{gid}/{img.file_loc}"
                            _path = AsyncPath(loc)
                            data = await self.download_file(loc)
                            ext = "." + guess_extension(data)
                            _mime = guess_mime(data)
                            if ext != _path.suffix:
                                _path2 = _path.with_suffix(ext)
                                img.file_loc = img.file_loc.replace(_path.suffix, _path2.suffix)
                                loc2 = f"{gid}/{img.file_loc}"
                                log.warning("{} is invalid. Renamed to {}", _path, _path2)
                                async with self.get_s3() as s3:
                                    s3: S3Client
                                    await s3.delete_object(Bucket="imgshare", Key=loc)
                                    await s3.put_object(Bucket="imgshare", Key=loc2, Body=data, ContentType=_mime)
                        finally:
                            _images.append(img)

                    async with anyio.create_task_group() as tg:
                        while True:
                            try:
                                img = settings.images.pop()
                            except IndexError:
                                break
                            else:
                                tg.start_soon(check_img, img)

                    settings.images = _images
                    __data.clear()
                    __data.update(settings.dict())

                if settings and not settings.images:
                    await self.config.guild_from_id(gid).clear()

            async with anyio.create_task_group() as tg:
                for gid in all_guilds:
                    tg.start_soon(check_guild_imgs, gid)

    @property
    def curl(self) -> CurlAsyncHTTPClient:
        return get_curl()

    async def first_word(self, msg: str) -> str:
        return msg.split(" ")[0].lower()

    async def get_prefix(self, message: discord.Message) -> str:
        try:
            guild = message.guild
        except AttributeError:
            guild = None
        content = message.content
        try:
            prefixes = await self.bot.get_valid_prefixes(guild)
        except AttributeError:
            prefix_list = await self.bot.command_prefix(self.bot, message)
            prefixes = sorted(prefix_list, key=lambda pfx: len(pfx), reverse=True)
        for p in prefixes:
            if content.startswith(p):
                return p
        msg = "No prefix found."
        raise ValueError(msg)

    async def part_of_existing_command(self, alias: str) -> bool:
        command = self.bot.get_command(alias)
        return command is not None

    async def get_image(self, alias: str, guild: Optional[discord.Guild] = None) -> dict:
        if guild is None:
            for image in await self.config.images():
                if image["command_name"].lower() == alias.lower():
                    return image
        else:
            for image in await self.config.guild(guild).images():
                if image["command_name"].lower() == alias.lower():
                    return image
        return {}

    async def local_perms(self, message: discord.Message) -> bool:
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
        if await self.bot.is_owner(message.author):
            return True
        try:
            return await self.bot.allowed_by_whitelist_blacklist(message.author)
        except AttributeError:
            whitelist = await self.bot.db.whitelist()
            if whitelist:
                return message.author.id in whitelist

            return message.author.id not in await self.bot.db.blacklist()

    async def check_ignored_channel(self, message: discord.Message) -> bool:
        ctx = await self.bot.get_context(message)
        return await self.bot.ignored_channel_or_guild(ctx)

    @asynccontextmanager
    async def get_s3(self):
        client = await self.session.create_client(
            "s3",
            aws_secret_access_key=os.environ["IDRIVE_SECRET_ACCESS_KEY"],
            aws_access_key_id=os.environ["IDRIVE_ACCESS_KEY_ID"],
            endpoint_url="https://n0w2.va.idrivee2-23.com",
        ).__aenter__()
        try:
            yield client

        finally:
            await client.__aexit__(None, None, None)

    async def save_image_location(self, url, msg: discord.Message, name: str, guild: discord.Guild | None) -> None:
        seed = "".join(random.sample(string.ascii_uppercase + string.digits, k=4))
        filename = f"{name}-{seed}-{get_filename_from_url(url)}"
        if guild is not None:
            directory = str(guild.id)
            cur_images = await self.config.guild(guild).images()
        else:
            directory = "global"
            cur_images = await self.config.images()
        name = name.lower()
        file_path = f"{directory}/{filename}"
        _path = AsyncPath(file_path)

        r = await self.curl.fetch(url)
        mime = guess_mime(r.body)
        ext = "." + guess_extension(r.body)
        if not mime or not ext:
            raise ValueError("Invalid media type")

        data = await optimize_target(r.body, ext)
        ext2 = "." + guess_extension(r.body)
        _path2 = _path.with_suffix(ext2)

        file = self.root / _path2
        await file.parent.mkdir(exist_ok=True)

        async def _run_upload():
            async with self.get_s3() as s3:
                await s3.put_object(Bucket="imgshare", Key=str(_path2), Body=data, ContentType=mime)
            await self.download_file(str(_path2))

        await asyncio.gather(file.write_bytes(data), _run_upload())
        new_entry = {
            "command_name": name,
            "count": 0,
            "file_loc": _path2.name,
            "author": msg.author.id,
            "author_name": str(msg.author),
            "created_at": time.time(),
        }
        cur_images.append(new_entry)
        if guild is not None:
            await self.config.guild(guild).images.set(cur_images)
        else:
            await self.config.images.set(cur_images)

    @commands.group(invoke_without_command=True, aliases=["addimage", "mediastore"])
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def savepic(self, ctx: commands.Context, name: str, image: ImageFinder = None) -> None:
        """Add an image for the bot to directly upload.

        `name` the command name used to post the image

        """
        if "list" in name.lower():
            return
        if len(name) > 32:
            return await ctx.send(embed=make_e("Name must be under 32 characters", 3))
        guild = ctx.message.guild
        if await self.check_command_exists(name, guild):
            return await ctx.send(embed=make_e(f"{name} is already in the list, try another!", 2))
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        try:
            url = image[0]
            async with ctx.typing():
                await self.save_image_location(url, ctx.message, name, guild)
        except FilesizeLimitExceeded as e:
            ctx.send(embed=make_e("I'm not going to save a file that large. Try something smaller 🤡", 3))
            raise e from e
        await ctx.send(embed=make_e(f" `;{name}` added as a command for this server", tip="delete files by going ;savepic del <name>"))

    @savepic.command(name="listglobal", hidden=True)
    async def list_image_global(self, ctx: commands.Context) -> None:
        # await self.bot.get_command
        await ctx.invoke(ctx.bot.get_command("savepic list"), image_loc="global")

    @checks.is_owner()
    @savepic.command(name="global", aliases=["addglobal", "saveglobal"], hidden=True)
    async def add_image_global(self, ctx: commands.Context, name: str, image: ImageFinder = None) -> None:
        """Add an image to direct upload globally.

        `name` the command name used to post the image

        """
        if len(name) > 32:
            return await ctx.send(embed=make_e("Name must be under 32 characters", 3))
        guild = ctx.message.guild
        guild = None
        if await self.check_command_exists(name, ctx.guild):
            return await ctx.send(embed=make_e(f"{name} is already in the list, try another!", 2))

        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        try:
            url = image[0]
            async with ctx.typing():
                await self.save_image_location(url, ctx.message, name, guild)
        except FilesizeLimitExceeded as e:
            ctx.send(embed=make_e("I'm not going to save a file that large. Try something smaller 🤡", 3))
            raise e from e
        await ctx.send(embed=make_e(f" `;{name}` added as a global command"))

    @savepic.command(name="list")
    async def listimages(self, ctx: commands.Context, image_loc="guild", server_id: discord.Guild = None) -> None:
        """List images added to bot."""
        if image_loc in ["global"]:
            image_list = await self.config.images()
        elif image_loc in ["guild", "server"]:
            guild = ctx.message.guild if server_id is None else self.bot.get_guild(server_id)
            image_list = await self.config.guild(guild).images()

        if image_list == []:
            return await ctx.send(embed=make_e("I do not have any images saved!", status="info"))
        image_list = sorted(image_list, key=lambda x: x["count"], reverse=True)
        post_list = [image_list[i : i + 25] for i in range(0, len(image_list), 25)]
        images = []
        for post in post_list:
            em = discord.Embed(timestamp=ctx.message.created_at)
            for image in post:
                info = "__Count__: " + f'**{image["count"]}**'
                em.add_field(name=image["command_name"], value=info)
            em.set_author(name=self.bot.user.display_name, icon_url=self.bot.user.avatar_url)
            em.set_footer(text="Page " + f"{post_list.index(post) + 1}/{len(post_list)}")
            images.append(em)
        await menu(ctx, images, DEFAULT_CONTROLS)

    @savepic.command(name="delete", aliases=["remove", "rem", "del"])
    @checks.mod_or_permissions(manage_messages=True)
    async def remimage(self, ctx: commands.Context, name: str) -> None:
        """Remove a selected images.

        `name` the command name used to post the image

        """
        guild = ctx.message.guild
        name = name.lower()
        if name not in [x["command_name"] for x in await self.config.guild(guild).images()]:
            await ctx.send(name + " is not an image for this guild!")
            return

        async with ctx.typing():
            async with self.config.guild(guild).images() as all_imgs:
                image = await self.get_image(name, guild)
                path = f'{str(guild.id)}/{image["file_loc"]}'
                async with self.get_s3() as s3:
                    await s3.delete_object(Bucket="imgshare", Key=path)
                all_imgs.remove(image)
            await ctx.send(embed=make_e(f"{name} has been deleted"))

    @checks.is_owner()
    @savepic.command(name="deleteglobal", aliases=["dg", "delglobal"], hidden=True)
    async def rem_image_global(self, ctx: commands.Context, name: str) -> None:
        """Remove a selected images.

        `name` the command name used to post the image

        """
        name = name.lower()
        if name not in [x["command_name"] for x in await self.config.images()]:
            await ctx.send(name + " is not a global image!")
            return

        async with ctx.typing():
            async with self.config.images() as all_imgs:
                image = await self.get_image(name)
                path = f'global/{image["file_loc"]}'
                async with self.get_s3() as s3:
                    await s3.delete_object(Bucket="imgshare", Key=path)
                all_imgs.remove(image)
            await ctx.send(embed=make_e(f"{name} has been deleted globally"))

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        if message.author.bot:
            return
        if len(message.content) < 2 or message.guild is None:
            return
        msg = message.content
        guild = message.guild
        channel: discord.TextChannel = message.channel
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        try:
            prefix = await self.get_prefix(message)
        except ValueError:
            return
        alias = await self.first_word(msg[len(prefix) :])
        if not await self.local_perms(message):
            return
        if not await self.global_perms(message):
            return
        if not await self.check_ignored_channel(message):
            return
        if alias in [x["command_name"] for x in await self.config.images()]:
            async with channel.typing():
                image = await self.get_image(alias)
                async with self.config.images() as list_images:
                    list_images.remove(image)
                    image["count"] += 1
                    list_images.append(image)
                path = "/global/" + image["file_loc"]
                name = image["file_loc"]
                payload = await self.download_file(path)
                file = discord.File(io.BytesIO(payload), filename=name)
                await channel.send(files=[file])
        if alias in [x["command_name"] for x in await self.config.guild(guild).images()]:
            async with channel.typing():
                image = await self.get_image(alias, guild)
                async with self.config.guild(guild).images() as guild_images:
                    guild_images.remove(image)
                    image["count"] += 1
                    guild_images.append(image)
                path = f"/{guild.id}/" + image["file_loc"]
                name = image["file_loc"]
                payload = await self.download_file(path)
                file = discord.File(io.BytesIO(payload), filename=name)
                await channel.send(files=[file])

    async def check_command_exists(self, command: str, guild: discord.Guild) -> bool:
        if command in [x["command_name"] for x in await self.config.guild(guild).images()]:
            return True
        elif await self.part_of_existing_command(command):
            return True
        return command in [x["command_name"] for x in await self.config.images()]
