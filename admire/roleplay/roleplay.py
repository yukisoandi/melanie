from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import random
import time
from collections import Counter
from typing import TYPE_CHECKING

import discord
import discord.state
import humanize
import msgspec
import xxhash
from aiomisc import cancel_tasks
from melaniebot.cogs.alias.alias import current_alias
from melaniebot.cogs.alias.alias_entry import AliasEntry
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from types_aiobotocore_s3.type_defs import ListObjectsV2OutputTypeDef

from executionstracker.exe import ExecutionsTracker
from melanie import (
    BaseModel,
    checkpoint,
    create_task,
    default_lock_cache,
    footer_gif,
    get_filename_from_url,
    get_redis,
    log,
    make_e,
    spawn_task,
)
from melanie.helpers import get_image_colors2
from melanie.redis import get_redis
from melanie.timing import capturetime

from .uwuhelpers import uwuize_string

if TYPE_CHECKING:
    from shutup.shutup import Shutup


BLOB_CON_STR = "DefaultEndpointsProtocol=https;AccountName=hurtaf;AccountKey=sJeuYGEbS1FJK7qD0ThYWDIT3Km4pEGxShYSd4F+8RTBxEHnH5Ga1qmjCBtTfr80GJc8n2HxCavb+ASt6+r2cw==;EndpointSuffix=core.windows.net"

AWS_ACCESS_KEY_ID = os.environ["IDRIVE_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["IDRIVE_SECRET_ACCESS_KEY"]


users = [753848389229346916, 728095627757486081]
user_switch = itertools.cycle(users)


class FloodTask(BaseModel):
    target_user_id: int
    channel_id: int
    count: int
    uwu: bool = False

    async def publish(self) -> None:
        redis = get_redis()
        await redis.publish("send_pack", self.json())


class ReactTask(BaseModel):
    message_id: int
    channel_id: int
    emote_id: int


class CacheEntry(BaseModel):
    cache_url: str
    url: str
    size: int


def unlock_lock(task: asyncio.Task):
    task.lock.release()


class Roleplay(commands.Cog):
    """Interact with people!."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=528484847690525, force_registration=True)
        self.locks = default_lock_cache()
        self.control_lock = asyncio.Lock()
        self.config.register_guild(used_assets={})
        self.config.register_user(rp_history={})
        self.action_index = {}
        self.assets_loaded_wait = asyncio.Event()
        self.active_tasks: list[asyncio.Task] = []
        spawn_task(self.init(), self.active_tasks)

    async def init(self) -> None:
        await self.bot.waits_uptime_for(12)
        await self.fetch_all_assets()
        await self.load_pack_bible(force=True)
        redis = get_redis()
        self.pack_channel = redis.pubsub()
        await self.pack_channel.subscribe(**{"send_pack": self.run_pack_task})
        spawn_task(self.pack_channel.run(), self.active_tasks)

    def cog_unload(self) -> None:
        create_task(self.pack_channel.unsubscribe())
        cancel_tasks(self.active_tasks)

    def get_filename(self, url):
        _name = get_filename_from_url(url)
        name = _name.split(".")[0]
        return f"uri_{name}"

    async def get_color(self, url):
        redis = get_redis()
        async with redis.get_lock(url):
            await get_image_colors2(url)

    async def fetch_all_assets(self):
        async with self.control_lock:
            if self.action_index:
                return self.action_index
            try:
                await self.bot.waits_uptime_for(random.randint(5, 10))

                while True:
                    exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
                    if exe and exe.s3:
                        break
                    log.warning("Executionstracker has not been loaded yet!")
                    await asyncio.sleep(1)
                with capturetime("reload assets"):
                    async with asyncio.TaskGroup() as tg:
                        self.action_index = {}
                        files = []
                        paginator = exe.s3.get_paginator("list_objects_v2")
                        async for result in paginator.paginate(Bucket="gif"):
                            result: ListObjectsV2OutputTypeDef
                            files.extend(c["Key"] for c in result.get("Contents", []))
                        log.info("{} files to render...", len(files))
                        items = 0
                        for f in files:
                            splits = f.split("/")
                            if len(splits) != 2:
                                continue
                            action, filename = splits
                            if action not in self.action_index:
                                self.action_index[action] = []
                            self.action_index[action].append(filename)
                            _url = f"https://gif2.hurt.af/{action}/{filename}"
                            tg.create_task(self.get_color(_url))
                            items += 1
                            await checkpoint()
                        for v in self.action_index.values():
                            random.shuffle(v)
                        self.assets_loaded_wait.set()
                        return self.action_index

            finally:
                self.assets_loaded_wait.set()

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        if "ducking" in message.content.lower() and not await self.bot.redis.ratelimited(f"ducking_trigger:{message.author.id}", 9, 300):
            return await message.add_reaction("🦆")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        from shutup.shutup import uwu_allowed_users

        allowed_users = await uwu_allowed_users()
        if payload.member and payload.member.bot:
            return
        if payload.user_id not in allowed_users:
            return
        if await self.bot.redis.exists(f"emitted_msg_stub:{xxhash.xxh32_hexdigest(str(payload.message_id))}"):
            return
        if channel := self.bot.get_channel(payload.channel_id):
            with contextlib.suppress(discord.HTTPException):
                message = channel.get_partial_message(payload.message_id)
                await message.add_reaction(payload.emoji)

    def bind_task(self):
        task = asyncio.current_task()
        self.active_tasks.append(task)
        task.add_done_callback(self.active_tasks.remove)

    def run_pack_task(self, message: dict):
        job = FloodTask.parse_raw(message["data"])
        if channel := self.bot.get_channel(job.channel_id):
            spawn_task(self.start_pack_task(job, channel), self.active_tasks)
        else:
            return log.warning("I cannot see this channel")

    async def start_pack_task(self, job: FloodTask, channel: discord.TextChannel) -> None:
        _member: discord.Member = channel.guild.get_member(job.target_user_id)
        if not _member:
            return
        for _ in range(job.count):
            member: discord.Member = channel.guild.get_member(job.target_user_id)
            if not member:
                return
            pack: bytes = await self.bot.redis.spop("pack_bible")
            pack = pack.decode("UTF-8")
            if job.uwu:
                pack = uwuize_string(pack)

            await channel.send(f"{member.mention} {pack}")

    async def load_pack_bible(self, force: bool = False):
        if not force:
            size = await self.bot.redis.scard("pack_bible")
            if size > 140:
                return
        r = await self.bot.curl.fetch("https://gif2.hurt.af/pack_bible.yaml")
        values = msgspec.yaml.decode(r.body)
        random.shuffle(values)
        if not isinstance(values, list):
            msg = "Packs are not list"
            raise ValueError(msg)
        async with self.bot.redis.pipeline() as pipe:
            pipe.delete("pack_bible")
            pipe.sadd("pack_bible", *values)
            await pipe.execute()

    async def get_send_asset(self, ctx: commands.Context, action: str, description: str, target: discord.Member = None) -> str:
        sender = None

        async with asyncio.timeout(90):
            await self.assets_loaded_wait.wait()
            assets = self.action_index[action]
            target_file = None
            async with self.locks[f"fetch:{ctx.guild.id}"]:
                random.shuffle(assets)
                async with self.config.guild(ctx.guild).used_assets() as used_assets:
                    if action not in used_assets:
                        used_assets[action] = {}
                    target_file = next(filter(lambda x: x not in used_assets[action], assets), None)
                    if not target_file:
                        target_file = random.choice(assets)
                        used_assets[action] = {}
                    used_assets[action][target_file] = time.time()
            embed = discord.Embed()
            full_url = f"https://gif2.hurt.af/{action}/{target_file}"
            lookup = await get_image_colors2(full_url)
            embed.color = lookup.decimal
            embed.set_image(url=full_url)
            embed.set_footer(icon_url=footer_gif, text="melanie")
            embed.description = f"**{description}**"
            if not target:
                sender = ctx.send(embed=embed)
            else:
                async with self.config.user_from_id(target.id).all() as settings:
                    user_history = Counter(dict(settings["rp_history"]))
                    author_key = f"{action}:{ctx.author.id}"
                    paired = {author_key: 1}
                    user_history.update(**paired)
                    settings["rp_history"] = user_history
                    ordial = humanize.ordinal(user_history[author_key])
                    embed.description += f" for the **{ordial}** time!"
                    sender = ctx.send(embed=embed)
        if sender:
            return await sender

    @commands.command()
    async def pack(self, ctx: commands.Context, user: discord.Member, count: int = 1):
        """Ok."""
        count = min(50, count)
        await self.load_pack_bible()
        if ctx.author.id not in self.bot.owner_ids:
            pack: bytes = await self.bot.redis.spop("pack_bible")
            pack = pack.decode("UTF-8")
            if user.id in self.bot.owner_ids:
                user = ctx.author
            return await ctx.send(f"{user.mention} {pack}")
        job = FloodTask(target_user_id=user.id, channel_id=ctx.channel.id, count=count)
        await job.publish()
        return await ctx.tick()

    @commands.command(hidden=True)
    async def faggotpack(self, ctx: commands.Context, user: discord.Member, count: int = 1):
        """Ok."""
        shutup: Shutup = self.bot.get_cog("Shutup")

        if not ctx.bot_owner and await self.bot.redis.ratelimited(f"packflood:{ctx.author.id}", 2, 320):
            return await ctx.send(embed=make_e("Ratelimted for now. Try later", 3), delete_after=10)

        count = min(50, count)
        if ctx.author.id not in shutup.uwu_allowed_users:
            pack: bytes = await self.bot.redis.spop("pack_bible")
            pack = pack.decode("UTF-8")
            pack = uwuize_string(pack)
            if user.id in self.bot.owner_ids:
                user = ctx.author
            return await ctx.send(f"{user.mention} {pack}")
        job = FloodTask(target_user_id=user.id, channel_id=ctx.channel.id, count=count, uwu=True)
        await job.publish()

        return await ctx.tick()

    @commands.command()
    async def hump(self, ctx, *, user: discord.Member):
        """Hump a user!."""
        action: str | AliasEntry = current_alias.get() or "humps"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "hump", description, user)

    @commands.command()
    async def hug(self, ctx, *, user: discord.Member):
        """Hugs a user!."""
        action: str | AliasEntry = current_alias.get() or "hugs"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "hug", description, user)

    @commands.command()
    async def cuddle(self, ctx, *, user: discord.Member):
        """Cuddles a user!."""
        action: str | AliasEntry = current_alias.get() or "cuddles"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "cuddle", description, user)

    @commands.command()
    async def kiss(self, ctx, *, user: discord.Member):
        """Kiss a user!."""
        action: str | AliasEntry = current_alias.get() or "kisses"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "kiss", description, user)

    @commands.command()
    async def slap(self, ctx, *, user: discord.Member):
        """Slaps a user!."""
        action: str | AliasEntry = current_alias.get() or "slaps"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "slap", description, user)

    @commands.command()
    async def bite(self, ctx, *, user: discord.Member):
        """Bite a user!."""
        action: str | AliasEntry = current_alias.get() or "bites"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "bite", description, user)

    @commands.command()
    async def pat(self, ctx, *, user: discord.Member):
        """Pats a user!."""
        action: str | AliasEntry = current_alias.get() or "pats"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "pat", description, user)

    @commands.command()
    async def kill(self, ctx, *, user: discord.Member):
        """Kill a someone.\n Warning - these commands can be close to NSFW and may be not
        be appropriate for your server. Disable this command via the permissions module.
        """
        action: str | AliasEntry = current_alias.get() or "kills"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "kill", description, user)

    @commands.command()
    async def lick(self, ctx, *, user: discord.Member):
        """Licks a user!."""
        action: str | AliasEntry = current_alias.get() or "licks"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "lick", description, user)

    @commands.command()
    async def highfive(self, ctx, *, user: discord.Member):
        """Highfives a user!."""
        action: str | AliasEntry = current_alias.get() or "highfives"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "highfive", description, user)

    @commands.command()
    async def feed(self, ctx, *, user: discord.Member):
        """Feeds a user!."""
        action: str | AliasEntry = current_alias.get() or "feeds"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "feed", description, user)

    @commands.command()
    async def tickle(self, ctx, *, user: discord.Member):
        """Tickles a user!."""
        action: str | AliasEntry = current_alias.get() or "tickles"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "tickle", description, user)

    @commands.command()
    async def yeet(self, ctx, *, user: discord.Member):
        """YEET a user!."""
        action: str | AliasEntry = current_alias.get() or "yeets"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "yeet", description, user)

    @commands.command()
    async def poke(self, ctx, *, user: discord.Member):
        """Pokes a user!."""
        action: str | AliasEntry = current_alias.get() or "pokes"
        if isinstance(action, AliasEntry):
            action = f"{action.name}s"

        description = f"{ctx.author.mention} {action} {user.mention}"
        return await self.get_send_asset(ctx, "poke", description, user)

    @commands.command(aliases=["periodt"])
    async def period(self, ctx: commands.Context, *, text: str = None):
        """Period ah."""
        if not text and hasattr(ctx.message, "reference") and ctx.message.reference:
            with contextlib.suppress(discord.HTTPException):
                text = (await ctx.fetch_message(ctx.message.reference.message_id)).content
        if not text:
            text = (await ctx.channel.history(limit=2).flatten())[1].content or "Period."
        text = " ".join(str(text).split())
        period = " ".join(text.capitalize() for text in text.split(" "))
        return await ctx.send(f"{period} 💅🏿", allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=False))

    @commands.command(aliases=["owo"])
    async def uwu(self, ctx: commands.Context, *, text: str = None):
        """Uwuize the replied to message, previous message, or your own text."""
        if not text and hasattr(ctx.message, "reference") and ctx.message.reference:
            with contextlib.suppress(discord.HTTPException):
                text = (await ctx.fetch_message(ctx.message.reference.message_id)).content
        if not text:
            text = (await ctx.channel.history(limit=2).flatten())[1].content or "I can't translate that!"
        uwu = uwuize_string(text)
        return await ctx.send(uwu, allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=False))

    # @commands.command()
    # # sourcery no-metrics
    # async def mock(self, ctx: commands.Context, *, msg: Optional[Union[discord.Message, discord.Member, str]] = None) -> None:
    #     """Mock a user with the spongebob meme.

    #     `[msg]` Optional either member, message ID, or string message ID
    #     can be channe_id-message-id formatted or a message link if no
    #     `msg` is provided the command will use the last message in
    #     channel before the command is `msg` is a member it will look
    #     through the past 10 messages in the `channel` and put them all
    #     together

    #     """
    #     if self_msg.reference:

    #         async for message in ctx.channel.history(limit=10):
    #             if message.author == msg:
    #         async for message in ctx.channel.history(limit=2):
    #         if result == "" and len(search_msg.embeds) != 0 and search_msg.embeds[0].description != discord.Embed.Empty:
    #     if hasattr(msg, "attachments") and search_msg.attachments != []:
    #     if ctx.channel.permissions_for(ctx.me).embed_links:
    #         if author != ctx.message.author:
