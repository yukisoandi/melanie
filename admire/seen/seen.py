from __future__ import annotations

import asyncio
import datetime
import random
import threading
import time
from contextlib import suppress
from typing import Any, Optional, Union

import asyncpg
import discord
import orjson
from anyio.abc import TaskGroup
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from executionstracker.exe import ExecutionsTracker
from melanie import (
    BaseModel,
    cancel_tasks,
    checkpoint,
    default_lock_cache,
    fetch_gif_if_tenor,
    get_redis,
    log,
    spawn_task,
)
from melanie.helpers import get_image_colors2
from melanie.timing import capturetime

default_global = {"schema_version": 1}
default_member = {"seen": None}


class MelanieMessage(BaseModel):
    message_id: str
    created_at: datetime.datetime
    user_name: str
    user_id: str
    user_discrim: Optional[str]
    user_avatar: Optional[str]
    user_nick: Optional[str]
    guild_name: str
    guild_id: str
    channel_id: str
    channel_name: str
    content: str
    reference: Optional[str]
    embeds: Optional[Any]
    insert_source: str
    bot: bool


def visit(p, k, v):
    if v == "Embed.Empty":
        return False
    return False if k == "color" else bool(v != {})


class Seen(commands.Cog):
    """Shows last time a user was seen in chat."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 2784481001, force_registration=True)
        self.config.register_global(**default_global)
        self.config.register_member(**default_member)
        self.locks = default_lock_cache(100_000_000)
        self.MEMBER = "MEMBER"
        self.pool: asyncpg.Pool = self.bot.driver._pool
        self.control_lock = threading.RLock()
        self.locks = default_lock_cache()
        self.insert_lock = asyncio.Lock()
        self.load_sem = asyncio.BoundedSemaphore(30)
        self.active_tasks = []
        self.seen_queue = {}
        self.redis = get_redis()
        self.lookups = {}
        spawn_task(self.insert_seen(), self.active_tasks)

    async def notice(self, member: discord.Member) -> None:
        if not hasattr(member, "guild"):
            return
        if not member.guild:
            return
        if member.bot:
            return
        jsonb = orjson.dumps({"seen": time.time()}).decode("utf-8")
        self.seen_queue[f"{member.guild.id}{member.id}"] = (member.guild.id, member.id, str(jsonb), str(jsonb))
        img_url = str(member.avatar_url).replace(".webp", ".gif") if member.is_avatar_animated() else str(member.avatar_url).replace(".webp", ".png")

        if img_url not in self.lookups:
            self.lookups[img_url] = await get_image_colors2(img_url)

    async def load_channel(self, channel: discord.TextChannel, tg: TaskGroup):
        if await self.redis.get(f"seenload:{channel.id}"):
            return
        async with self.load_sem:
            with capturetime(f"load {channel}"):
                async for m in channel.history(limit=9000):
                    spawn_task(self.insert_message(m, False), self.active_tasks)
                    await checkpoint()

                await self.redis.set(f"seenload:{channel.id}", 1, ex=3200)

    async def preload(self):
        async def run():
            chs = []
            async with asyncio.TaskGroup() as tg:
                for guild in self.bot.guilds:
                    for channel in guild.channels:
                        if isinstance(channel, discord.TextChannel):
                            chs.append(channel)
                random.shuffle(chs)
                [tg.create_task(self.load_channel(channel, tg)) for channel in chs]

        await spawn_task(run(), self.active_tasks)

    def cog_unload(self):
        self.deletes_db.stop(True)
        cancel_tasks(self.active_tasks)

    def bind_task(self):
        task = asyncio.current_task()
        self.active_tasks.append(task)
        task.add_done_callback(self.active_tasks.remove)

    async def insert_seen(self):
        while True:
            with log.catch(exclude=asyncio.CancelledError):
                await asyncio.sleep(1.2)
                if self.seen_queue:
                    stmt = 'INSERT INTO "Seen.2784481001"."MEMBER" (primary_key_1, primary_key_2, json_data ) VALUES ( $1, $2, $3 ) ON CONFLICT(primary_key_1,primary_key_2) DO UPDATE SET json_data = $4 ;'
                    values = list(self.seen_queue.values())
                    self.seen_queue.clear()
                    await self.pool.executemany(stmt, values)

    async def insert_message(self, message: discord.Message, notice: bool):
        if message.guild and message.guild.id == 899833727490867272:
            return

        if message.author.bot and "melanie" not in message.author.name:
            return
        splits = str(message.author).split("#")
        username = splits[0]
        discrim = splits[1]
        user_: discord.User = self.bot.get_user(int(message.author.id))
        avatar = user_.avatar if user_ else None
        embeds_out = []
        if hasattr(message, "embeds") and message.embeds:
            for embed in message.embeds:
                embed: discord.Embed
                data2 = {"title": str(embed.title), "description": str(embed.description), "url": str(embed.url), "timestamp": str(embed.timestamp)}
                if embed.color:
                    data2["color"] = int(embed.color.value)

                if embed.footer and hasattr(embed.footer, "text"):
                    data2["footer"] = {"text": str(embed.footer.text)}
                    if hasattr(embed.footer, "icon_url"):
                        data2["footer"]["icon_url"] = str(embed.footer.icon_url)

                if embed.image:
                    data2["image"] = {"url": embed.image.url, "width": embed.image.width, "height": embed.image.height}

                if embed.thumbnail:
                    data2["thumbnail"] = {"url": embed.thumbnail.url, "width": embed.thumbnail.width, "height": embed.thumbnail.height}

                if embed.author:
                    data2["author"] = {"name": str(embed.author.name)}
                    if hasattr(embed.author, "icon_url") and embed.author.icon_url:
                        data2["author"]["icon_url"] = str(embed.author.icon_url)

                if embed.fields:
                    fields = []
                    for f in embed.fields:
                        data = {"name": str(f.name), "value": str(f.value), "inline": str(f.inline)}
                        fields.append(data)

                    data2["fields"] = fields

                payload = orjson.loads(orjson.dumps(data2))

                for k, v in payload.items():
                    await checkpoint()
                    if v == "Embed.Empty":
                        del data2[k]
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            if v2 == "Embed.Empty":
                                del data2[k][k2]
                embeds_out.append(data2)

        embeds_out = orjson.dumps(embeds_out).decode() if embeds_out else None
        try:
            nick = message.author.nick
        except AttributeError:
            nick = None
        if message.content and "c" in message.content and "http" in message.content:
            spawn_task(fetch_gif_if_tenor(message.content), self.active_tasks)
        guild_id = message.guild.id if message.guild else 9
        content = str(message.content).encode("UTF-8", "ignore").decode("UTF-8", "ignore")

        #     class MelanieMessage(BaseModel):

        msg = MelanieMessage(
            message_id=str(message.id),
            created_at=message.created_at,
            user_name=username,
            user_id=str(message.author.id),
            user_discrim=discrim,
            user_avatar=avatar,
            user_nick=nick,
            guild_name=str(message.guild),
            guild_id=str(guild_id),
            channel_id=str(message.channel.id),
            channel_name=str(message.channel),
            content=content,
            reference=message.reference.message_id if message.reference else None,
            embeds=embeds_out,
            insert_source=str(self.bot.user),
            bot=message.author.bot,
        )
        await self._sql_insert_message(msg)
        if notice:
            await self.notice(message.author)

    async def _sql_insert_message(self, msg: MelanieMessage):
        stmt = """insert into guild_messages (message_id, created_at, user_name, user_id, user_discrim, user_avatar, user_nick,
                            guild_name, guild_id, channel_id, channel_name, content, reference, embeds, insert_source, bot) values ($1, $2, $3, $4,$5, $6,$7,$8,$9,$10,$11,$12,$13,$14,$15, $16) on conflict (guild_id, created_at, message_id) do nothing"""
        with suppress(asyncpg.exceptions.CharacterNotInRepertoireError):
            exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
            while not exe or not exe.database:
                log.error("No DB")
                await asyncio.sleep(1)
                exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
            await exe.database.execute(stmt, *list(msg.dict().values()))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await checkpoint()
        spawn_task(self.insert_message(message, notice=True), self.active_tasks)

    @commands.Cog.listener()
    async def on_typing(self, channel: discord.abc.Messageable, user: Union[discord.User, discord.Member], when: datetime.datetime) -> None:
        await self.notice(user)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self.notice(after.author)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]) -> None:
        await self.notice(user)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: Union[discord.Member, discord.User]) -> None:
        await self.notice(user)

    @commands.guild_only()
    @commands.command(name="seen")
    async def _seen(self, ctx, author: discord.Member):
        """Shows last time a user was seen in chat."""
        member_seen_config = await self.config.member(author).seen()
        if not member_seen_config:
            embed = discord.Embed(colour=discord.Color.red(), title="I haven't seen that user yet.")
            return await ctx.send(embed=embed)
        else:
            member_seen = member_seen_config
        now = int(time.time())
        time_elapsed = int(now - member_seen)
        output = self._dynamic_time(time_elapsed)

        if output[2] < 2:
            ts = "just now"
        else:
            ts = ""
            if output[0] == 1:
                ts += f"{output[0]} day, "
            elif output[0] > 1:
                ts += f"{output[0]} days, "
            if output[1] == 1:
                ts += f"{output[1]} hour and "
            elif output[1] > 1:
                ts += f"{output[1]} hours and "
            if output[2] == 1:
                ts += f"{output[2]} minute ago"
            elif output[2] > 1:
                ts += f"{output[2]} minutes ago"
        em = discord.Embed(colour=discord.Color.green(), description=f"{author.mention} was active on this server {ts}")
        await ctx.send(embed=em)

    @staticmethod
    def _dynamic_time(time_elapsed):
        m, s = divmod(time_elapsed, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        return d, h, m
