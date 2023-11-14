from __future__ import annotations

import asyncio
import copy
import io
from contextlib import suppress
from typing import Optional, Union

import arrow
import discord
import orjson
from boltons.timeutils import relative_time
from discord.ext.commands import Context
from discord.sticker import Sticker
from discord.utils import find
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from redis.exceptions import RedisError

from executionstracker.exe import ExecutionsTracker
from melanie import (
    SHARED_API_HEADERS,
    CurlError,
    cancel_tasks,
    default_lock_cache,
    footer_gif,
    get_redis,
    log,
    make_e,
)
from melanie.core import spawn_task
from melanie.models.sharedapi.discord import (
    DeletionConfirmation,
    SharedApiSocket,
    SnipeDeleteRequest,
)
from seen.seen import MelanieMessage
from snipe.models import (
    SNIPE_TTL,
    MessageSnipe,
    ReactionSnipe,
    StickerMessageSnipe,
    save_attachment,
)


class Snipe(commands.Cog):
    """Snipe the last message from a server."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=95932766180343808, force_registration=True)
        self.config.register_guild(**{"toggle": True, "immuneRole": None})
        self.snipe_locks = default_lock_cache()
        self.active_tasks = []
        self.shared_api_socket = SharedApiSocket(SHARED_API_HEADERS)
        spawn_task(self.shared_api_socket.run(self.sharedapi_clearsnipe), self.active_tasks)

    async def sharedapi_clearsnipe(self, data: SnipeDeleteRequest) -> int | None:
        async with self.snipe_locks[data.channel_id]:
            if channel := self.bot.get_channel(data.channel_id):
                num_snipes = 0
                redis_key = MessageSnipe.make_channel_key(channel)

                with suppress(RedisError):
                    num_snipes = await self.bot.redis.llen(redis_key)

                await self.bot.redis.delete(redis_key)

                return num_snipes

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    @checks.has_permissions(manage_messages=True)
    @commands.max_concurrency(1, commands.BucketType.channel)
    @commands.command(aliases=["cs"])
    async def clearsnipe(self, ctx: Context, channel: discord.TextChannel = None):
        """Force delete the snipe cache in a channel."""
        if not channel:
            channel = ctx.channel
        api_delete = spawn_task(self.shared_api_socket.submit_snipedel_request(channel_id=channel.id), self.active_tasks)
        redis_key = MessageSnipe.make_channel_key(channel)
        num_snipes = 0
        with suppress(RedisError):
            num_snipes = await self.bot.redis.llen(redis_key)
        await self.bot.redis.delete(redis_key)
        tip = f"{num_snipes} snipe(s) removed." if num_snipes else ""
        msg = await ctx.send(embed=make_e(f"The snipe cache for {channel} was purged", tip=tip))
        if api_delete:
            with suppress(asyncio.TimeoutError, discord.HTTPException, CurlError):
                async with asyncio.timeout(3):
                    api_snipes: DeletionConfirmation = await api_delete
                    if api_snipes.confirmed and api_snipes.deleted_items:
                        tip = f"{tip} Bleed's snipe cache has also been cleared!"
                        await msg.edit(embed=make_e(f"The snipe cache for {channel} was purged", tip=tip))

    async def save_deleted_message(self, payload: Union[discord.RawMessageDeleteEvent, discord.Message]) -> None:
        redis = get_redis()

        if isinstance(payload, discord.Message):
            delete_time = 0
            channel_id = payload.channel.id
            message_id = payload.id
            guild_id = payload.guild.id
            data = {"id": payload.id, "guild_id": guild_id, "channel_id": channel_id, "message_id": message_id}
            _message = copy.copy(payload)
            payload = discord.RawMessageDeleteEvent(data)
            payload.cached_message = _message

        else:
            delete_time = arrow.utcnow().timestamp()
        message_id = payload.message_id
        guild_id = payload.guild_id
        channel_id = payload.channel_id
        if not payload.cached_message:
            return
        message: discord.Message = payload.cached_message
        if message.content.strip() == ".pick":
            return
        if message.author.bot and not message.attachments:
            return
        snipe = MessageSnipe(
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=message.guild.id,
            content=message.content,
            user_id=message.author.id,
            user_name=str(message.author),
            avatar_icon_url=str(message.author.avatar_url),
            created_at=arrow.get(message.created_at).timestamp(),
            deleted_at=delete_time,
        )
        stickers: list[Sticker] = message.stickers
        for s in stickers:
            snipe.stickers.append(StickerMessageSnipe(id=s.id, name=s.name, format=s.format.name))
        assets = await asyncio.gather(*[save_attachment(i) for i in message.attachments])
        for i in assets:
            snipe.attachment_keys.append(i.key)

        if not snipe.content and not snipe.attachment_keys and not snipe.stickers:
            return log.debug(f"Dead message {snipe}")
        redis_key = MessageSnipe.make_channel_key(channel_id)
        snipe_size = await redis.rpush(redis_key, orjson.dumps(snipe.dict()))
        if snipe_size > 100:
            await redis.ltrim(redis_key, -49, -1)
        await redis.expire(redis_key, SNIPE_TTL)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if not payload.guild_id:
            return
        await self.save_deleted_message(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        timestamp = arrow.utcnow().timestamp()
        if not payload.guild_id:
            return

        redis = get_redis()
        user = await self.bot.get_or_fetch_user(payload.user_id)
        redis_key = ReactionSnipe.make_channel_key(payload.channel_id)
        sniped_react = ReactionSnipe(
            emote_name=str(payload.emoji),
            emote_url=str(payload.emoji.url) if payload.emoji.is_custom_emoji() else None,
            message_id=payload.message_id,
            user_id=payload.user_id,
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            user_name=str(user),
            timestamp=timestamp,
        )

        await redis.rpush(redis_key, orjson.dumps(sniped_react.dict()))
        await redis.ltrim(redis_key, -49, -1)
        await redis.expire(redis_key, 14400)

    @commands.cooldown(2, 5, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.channel)
    @commands.command(aliases=["es"])
    async def editsnipe(self, ctx: Context, message: Optional[discord.Message]):
        """View the original message before it was edited."""
        async with ctx.typing(), asyncio.timeout(10):
            _msg: discord.Message = ctx.message
            if not message and not _msg.reference:
                channel: discord.TextChannel = ctx.channel
                async for m in channel.history(limit=100):
                    m: discord.Message
                    if m.edited_at and not m.author.bot:
                        message = m
                        break
                if not message:
                    return await ctx.send_help()
            if not message:
                _channel: discord.TextChannel = self.bot.get_channel(_msg.reference.channel_id)
                message = find(lambda x: x.id == _msg.reference.message_id, self.bot.cached_messages)
            if not message:
                message = await _channel.fetch_message(_msg.reference.message_id)
            if not message.edited_at:
                return await ctx.send(embed=make_e("This message has not been edited", 2))
            stmt = "select * from guild_messages where message_id = $1 and guild_id = $2"
            exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
            res = await exe.database.fetchrow(stmt, str(message.id), str(message.guild.id))
            if not res:
                return await ctx.send(embed=make_e("Unable to find the original message", 2))
            data = MelanieMessage(**res)
            embed = discord.Embed()
            embed.color = 3092790
            av_ext = "gif" if data.user_avatar.startswith("a_") else "png"
            embed.set_author(name=str(data.user_name), icon_url=f"https://cdn.discordapp.com/avatars/{data.user_id}/{data.user_avatar}.{av_ext}")
            embed.description = data.content
            embed.set_footer(icon_url=footer_gif, text=f"edited {relative_time(arrow.get(message.edited_at).naive, arrow.utcnow().naive)}")
            return await ctx.send(embed=embed)

    @commands.cooldown(2, 5, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.channel)
    @commands.command()
    async def snipe(self, ctx: Context, number: int = 1):
        """Shows the last deleted message from a specified channel.

        If the user sniping has manage message permissions they will be
        able to snipe messages filtered by bots.

        """
        if await self.bot.redis.get(f"shutup_lock:{ctx.channel.id}"):
            return await ctx.send(embed=make_e("This command is disabled for ~ 30 seconds while a user is under shutup or uwulock", 2))
        if number > 5:
            return await ctx.send(embed=make_e("You can only snipe up to 5 message batches", status=3, tip="rerun the command with up to 5 requested"))
        channel: discord.TextChannel = ctx.channel
        redis = get_redis()
        redis_key = MessageSnipe.make_channel_key(ctx.channel)
        async with asyncio.timeout(15):
            count_at_call = await redis.llen(redis_key)

            is_message_admin = channel.permissions_for(ctx.author).manage_messages or ctx.author.id in self.bot.owner_ids
            num_requested = number
            number = min(count_at_call, num_requested)
            immune_role_id = await self.config.guild(ctx.guild).immuneRole()
            immune_role: discord.Role = ctx.guild.get_role(immune_role_id)
            immune_ids = [m.id for m in immune_role.members] if immune_role else []
            snipes_list: list[MessageSnipe] = []
            immune_failures = 0
            filter_failures = 0
            rejected_blobs: list[bytes] = []
            if not await redis.llen(redis_key):
                return await ctx.reply(embed=make_e("No sniped messages available", 2, tip="i only cache messages for up to 8 hours"))
            async with ctx.typing():
                snipes = await redis.rpop(redis_key, number)
                for data in snipes:
                    channelsnipe = await MessageSnipe.from_cache(data)
                    # if ctx.author.id in self.bot.owner_ids:

                    if channelsnipe.user_id in immune_ids:
                        immune_failures += 1
                        rejected_blobs.append(data)
                        continue
                    if not is_message_admin and channelsnipe.was_bot_filtered:
                        filter_failures += 1
                        rejected_blobs.append(data)
                        continue
                    else:
                        snipes_list.append(channelsnipe)
                if not snipes_list:
                    embed = make_e("No snipes able to be viewed by you at the moment", 2, tip="messages cached for 8h")
                    if immune_failures:
                        if len(snipes) == 1:
                            return await ctx.send(embed=make_e("That message author has the snipe immunity role and cannot be sniped", 2))
                        embed.add_field(name="author is immune", value=immune_failures)
                    if filter_failures:
                        if len(snipes) == 1:
                            await redis.rpush(redis_key, *rejected_blobs)
                            return await ctx.send(embed=make_e("Only moderators may snipe back filtered content", 2))
                        embed.add_field(name="bot filtered", value=filter_failures)
                    return await ctx.send(embed=embed, delete_after=3)
                if number > len(snipes_list) and immune_failures or filter_failures:
                    embed = make_e(f"Only sniping back {len(snipes_list)} message instead of {number} requested", 2)
                    if immune_failures:
                        embed.add_field(name="author immunte", value=immune_failures)
                    if filter_failures:
                        embed.add_field(name="bot filtered", value=filter_failures)
                    await ctx.send(embed=embed, delete_after=3)
                snipe_count = int(count_at_call)
                for channelsnipe in snipes_list:
                    try:
                        snipe_count -= 1
                        if not channelsnipe.content and not channelsnipe.stickers and not channelsnipe.attachment_keys:
                            await ctx.send(embed=make_e("No content in this message.", 2, tip="might contain default stickers or invisible characters"))
                            continue
                        embed = discord.Embed(description=channelsnipe.content, color=3092790)
                        sent_delta = relative_time(arrow.get(channelsnipe.created_at).naive, arrow.utcnow().naive)
                        count_str = f" | {snipe_count} { 'msg' if snipe_count > 0 else 'msgs'} in snipe" if snipe_count else ""
                        embed.set_footer(icon_url=footer_gif, text=f"sent {sent_delta} {count_str}")

                        if channelsnipe.avatar_icon_url:
                            embed.set_author(name=channelsnipe.user_name, icon_url=channelsnipe.avatar_icon_url)
                        else:
                            embed.set_author(name=channelsnipe.user_name)
                        attachments = [item.to_discord_file() for item in channelsnipe.loaded_attachments]
                        if channelsnipe.stickers:
                            sticker = channelsnipe.stickers[0]
                            if sticker.format == "apng":
                                img_bytes = await sticker.convert_to_gif(self.bot)
                                file = discord.File(io.BytesIO(img_bytes), filename=f"SnipedSticker{sticker.id}.gif")
                                attachments.append(file)
                                embed.set_thumbnail(url=f"attachment://{file.filename}")
                            else:
                                embed.set_thumbnail(url=sticker.url)
                        await ctx.send(embed=embed, files=attachments)
                    except asyncio.CancelledError:
                        raise

                    except Exception:
                        await redis.rpush(redis_key, orjson.dumps(channelsnipe.dict()))
                        raise
                    finally:
                        if rejected_blobs:
                            await redis.rpush(redis_key, *rejected_blobs)

    @commands.cooldown(rate=1, per=3, type=commands.BucketType.channel)
    @commands.command(aliases=["rs"])
    async def rsnipe(self, ctx: Context, number: int = 1, channel: Optional[discord.TextChannel] = None):
        channel: discord.TextChannel = ctx.channel
        if number > 5:
            return await ctx.send(embed=make_e("Only the past 5 deleted messages can be sniped", status=3))
        channel: discord.TextChannel = channel or ctx.channel
        redis = get_redis()
        redis_key = ReactionSnipe.make_channel_key(channel)
        snipe_count = await redis.llen(redis_key)
        if not snipe_count:
            return await ctx.send(embed=make_e("No react snipe cache for this channel", status=2, tip="reactions cached for 4h"), delete_after=4)
        for _ in range(number):
            data = await redis.rpop(redis_key)
            if not data:
                return await ctx.send(
                    embed=make_e("No more sniped reactions", status=2, tip="reactions can only be sniped for up to 3 minutes!"),
                    delete_after=4,
                )
            channelsnipe = ReactionSnipe(**orjson.loads(data))
            em = discord.Embed(color=3092790)
            em.timestamp = arrow.get(channelsnipe.timestamp).datetime
            em.description = f"{channelsnipe.user_name} reacted with {channelsnipe.emote_name} and deleted it\n\n[jump link]({channelsnipe.message_link})"
            if channelsnipe.emote_url:
                em.set_thumbnail(url=channelsnipe.emote_url)

            em.set_footer(text="melanie | reaction snipe", icon_url=footer_gif)
            await ctx.send(embed=em)

    @checks.has_permissions(administrator=True)
    @commands.group()
    async def snipeset(self, ctx) -> None:
        """Group Command for Snipe Settings."""

    @snipeset.command()
    async def immune(self, ctx: commands.Context, role: discord.Role = None):
        """Set a role who's members messages cannot be sniped."""
        if not role:
            await self.config.guild(ctx.guild).immuneRole.set(None)
            return await ctx.send(embed=make_e("Immune role removed."))

        await self.config.guild(ctx.guild).immuneRole.set(role.id)
        await ctx.send(embed=make_e(f"{role.mention} has been added."))

    @snipeset.command()
    async def enable(self, ctx, state: bool) -> None:
        """Enable or disable sniping.

        State must be a bool or one of the following: True/False,
        On/Off, Y/N

        """
        if state:
            await self.config.guild(ctx.guild).toggle.set(True)
            await ctx.send(f"Sniping has been enabled in {ctx.guild}.")
        else:
            await self.config.guild(ctx.guild).toggle.set(False)
            await ctx.send(f"Sniping has been disabled in {ctx.guild}.")
