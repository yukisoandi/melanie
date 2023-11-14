from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
import textwrap
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any, Generator, Optional, Union, cast

import aiohttp
import aiopg
import arrow
import asyncpg
import discord.http
import httpx
import orjson
from aiobotocore.session import AioSession
from anyio import CapacityLimiter
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import Config, VersionInfo, commands, modlog, version_info
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import (
    escape,
    humanize_list,
    humanize_timedelta,
    pagify,
)
from types_aiobotocore_s3.client import S3Client
from xxhash import xxh32_hexdigest

from executionstracker.exe import ExecutionsTracker
from melanie import checkpoint, create_task, footer_gif, get_redis, log
from melanie.core import spawn_task
from seen.seen import MelanieMessage


class CommandPrivs(Converter):
    """Converter for command privliges."""

    async def convert(self, ctx: commands.Context, argument: str) -> str:
        levels = ["MOD", "ADMIN", "BOT_OWNER", "GUILD_OWNER", "NONE"]
        result = None
        if argument.upper() in levels:
            result = argument.upper()
        if argument == "all":
            result = "NONE"
        if not result:
            msg = f"`{argument}` is not an available command permission."
            raise BadArgument(msg)
        return result


class EventChooser(Converter):
    """Converter for command privliges."""

    async def convert(self, ctx: commands.Context, argument: str) -> str:
        if argument.startswith("member_"):
            argument = argument.replace("member_", "user_")
        options = [
            "message_edit",
            "message_delete",
            "user_change",
            "role_change",
            "role_create",
            "role_delete",
            "voice_change",
            "user_join",
            "user_left",
            "channel_change",
            "channel_create",
            "channel_delete",
            "guild_change",
            "emoji_change",
            "commands_used",
            "invite_created",
            "invite_deleted",
        ]

        result = argument.lower() if argument.lower() in options else None
        if not result:
            msg = f"`{argument}` is not an available event option."
            raise BadArgument(msg)
        return result


class EventMixin:
    """Handles all the on_event data."""

    config: Config
    bot: Melanie
    settings: dict[int, Any]
    _ban_cache: dict[int, list[int]]
    htx: httpx.AsyncClient
    bulk_submit_tasks: dict[int, asyncio.Task]
    channel_locks = defaultdict(asyncio.Lock)
    bulk_counter: Counter
    single_counter: Counter
    count_lock: asyncio.Lock
    redis = get_redis()
    pool: aiopg.Pool
    s3_session: AioSession
    purge_tasks: dict[int, CapacityLimiter]
    locks: defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def get_s3(self) -> Generator[S3Client, None, None]:
        client = await self.s3_session.create_client(
            "s3",
            aws_secret_access_key=os.environ["IDRIVE_SECRET_ACCESS_KEY"],
            aws_access_key_id=os.environ["IDRIVE_ACCESS_KEY_ID"],
            endpoint_url="https://n0w2.va.idrivee2-23.com",
        ).__aenter__()
        try:
            yield client

        finally:
            await client.__aexit__(None, None, None)

    async def get_event_colour(self, guild: discord.Guild, event_type: str, changed_object: Optional[discord.Role] = None) -> discord.Colour:
        if guild.text_channels:
            cmd_colour = await self.bot.get_embed_colour(guild.text_channels[0])
        else:
            cmd_colour = discord.Colour()
        defaults = {
            "message_edit": discord.Colour.orange(),
            "message_delete": discord.Colour.dark_red(),
            "user_change": discord.Colour.greyple(),
            "role_change": changed_object.colour if changed_object else discord.Colour.blue(),
            "role_create": discord.Colour.blue(),
            "role_delete": discord.Colour.dark_blue(),
            "voice_change": discord.Colour.magenta(),
            "user_join": discord.Colour.green(),
            "user_left": discord.Colour.dark_green(),
            "channel_change": discord.Colour.teal(),
            "channel_create": discord.Colour.teal(),
            "channel_delete": discord.Colour.dark_teal(),
            "guild_change": discord.Colour.blurple(),
            "emoji_change": discord.Colour.gold(),
            "commands_used": cmd_colour,
            "invite_created": discord.Colour.blurple(),
            "invite_deleted": discord.Colour.blurple(),
        }
        colour = defaults[event_type]
        if self.settings[guild.id][event_type]["colour"] is not None:
            colour = discord.Colour(self.settings[guild.id][event_type]["colour"])
        return colour

    async def is_ignored_channel(self, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        ignored_channels = self.settings[guild.id]["ignored_channels"]
        if channel.id in ignored_channels:
            return True
        return bool(channel.category and channel.category.id in ignored_channels)

    async def member_can_run(self, ctx: commands.Context) -> bool:
        """Check if a user can run a command.

        This will take the current context into account, such as the
        server and text channel. https://github.com/Cog-
        Creators/Melanie-DiscordBot/blob/V3/release/3.0.0/melaniebot/cogs/pe
        rmissions/permissions.py

        """
        command = ctx.message.content.replace(ctx.prefix, "")
        com = ctx.bot.get_command(command)
        if com is None:
            return False
        try:
            testcontext = await ctx.bot.get_context(ctx.message, cls=commands.Context)
            to_check = [*reversed(com.parents)] + [com]
            can = False
            for cmd in to_check:
                can = await cmd.can_run(testcontext)
                if can is False:
                    break
        except (commands.CheckFailure, commands.DisabledCommand):
            can = False
        return can

    async def modlog_channel(self, guild: discord.Guild, event: str) -> discord.TextChannel:
        channel = None
        settings = self.settings[guild.id].get(event)
        if "channel" in settings and settings["channel"]:
            channel = guild.get_channel(settings["channel"])
        if channel is None:
            try:
                channel = await modlog.get_modlog_channel(guild)
            except RuntimeError as e:
                msg = "No Modlog set"
                raise RuntimeError(msg) from e

        return channel

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        # sourcery skip: merge-nested-ifs
        if not self.valid_event():
            return
        guild = ctx.guild
        if guild is None:
            return
        if version_info >= VersionInfo.from_str("3.4.0") and await self.bot.cog_disabled_in_guild(self, ctx.guild):
            return
        if guild.id not in self.settings:
            return
        if not self.settings[guild.id]["commands_used"]["enabled"]:
            return
        if await self.is_ignored_channel(ctx.guild, ctx.channel):
            return
        try:
            channel = await self.modlog_channel(guild, "commands_used")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n

        time = ctx.message.created_at
        message = ctx.message
        can_run = await self.member_can_run(ctx)
        try:
            privs = ctx.command.requires.privilege_level.name
            user_perms = ctx.command.requires.user_perms
            my_perms = ctx.command.requires.bot_perms
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if privs not in self.settings[guild.id]["commands_used"]["privs"]:
            log.debug(f"command not in list {privs}")
            return

        if privs == "ADMIN":
            admin_role_list = await ctx.bot.get_admin_roles(guild)
            role = f"{humanize_list([r.mention for r in admin_role_list])}\n{privs}\n" if admin_role_list != [] else ("Not Set\nADMIN\n")

        elif privs == "BOT_OWNER":
            role = humanize_list([f"<@!{_id}>" for _id in ctx.bot.owner_ids])
            role += f"\n{privs}\n"
        elif privs == "GUILD_OWNER":
            role = f"{guild.owner.mention}\n{privs}\n"
        elif privs == "MOD":
            mod_role_list = await ctx.bot.get_mod_roles(guild)
            role = f"{humanize_list([r.mention for r in mod_role_list])}\n{privs}\n" if mod_role_list != [] else ("Not Set\nMOD\n")

        else:
            role = f"everyone\n{privs}\n"
        if user_perms:
            role += humanize_list([perm.replace("_", " ").title() for perm, value in user_perms if value])
        if my_perms:
            i_require = humanize_list([perm.replace("_", " ").title() for perm, value in my_perms if value])
        infomessage = f"{self.settings[guild.id]['commands_used']['emoji']} `{message.created_at.strftime('%H:%M:%S')}` {message.author}(`{message.author.id}`) used the following command in {message.channel.mention}\n> {message.content}"
        if embed_links:
            embed = discord.Embed(
                description=f"{ctx.author.mention} {message.content}",
                colour=await self.get_event_colour(guild, "commands_used"),
                timestamp=time,
            )
            embed.add_field(name="Channel", value=message.channel.mention)
            embed.add_field(name="Can Run", value=str(can_run))
            embed.add_field(name="Requires", value=role)
            if i_require:
                embed.add_field(name="Bot Requires", value=i_require)
            author_title = f"{message.author} ({message.author.id})- Used a Command"
            embed.set_author(name=author_title, icon_url=message.author.avatar_url)
            await channel.send(embed=embed)
        else:
            await channel.send(infomessage[:2000])

    @commands.Cog.listener(name="on_raw_message_delete")
    async def on_raw_message_delete_listener(self, payload: discord.RawMessageDeleteEvent, *, check_audit_log: bool = True) -> None:
        # custom name of method used, because this is only supported in Melanie 3.1+
        if not self.valid_event():
            return
        guild_id = payload.guild_id
        if guild_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild.id not in self.settings:
            return

        async with self.count_lock:
            self.single_counter.update({str(guild_id): 1})

        settings = await self.config.guild(guild).message_delete()
        if not settings["enabled"]:
            return
        channel_id = payload.channel_id
        try:
            channel = await self.modlog_channel(guild, "message_delete")
        except RuntimeError:
            return
        if await self.is_ignored_channel(guild, guild.get_channel(channel_id)):
            return
        message = payload.cached_message
        if message is None:
            if settings["cached_only"]:
                return
            message_channel = guild.get_channel(channel_id)
            embed_links = True

            if embed_links:
                embed = discord.Embed(description="*Message's content unknown.*", colour=await self.get_event_colour(guild, "message_delete"))
                embed.add_field(name="Channel", value=message_channel.mention)
                embed.set_author(name="Deleted Message")
                await channel.send(embed=embed)
            else:
                infomessage = f"{settings['emoji']} `{datetime.datetime.utcnow().strftime('%H:%M:%S')}` A message was deleted in {message_channel.mention}"
                await channel.send(f"{infomessage}\n> *Message's content unknown.*")
            return
        await self._cached_message_delete(message, guild, settings, channel, check_audit_log=check_audit_log)

    async def _cached_message_delete(
        self,
        message: discord.Message,
        guild: discord.Guild,
        settings: dict,
        channel: discord.TextChannel,
        *,
        check_audit_log: bool = True,
    ) -> None:
        if message.author.bot and not settings["bots"]:
            # return to ignore bot accounts if enabled
            return
        if message.content == "" and message.attachments == []:
            return
        time = message.created_at
        perp = None
        if check_audit_log:
            action = discord.AuditLogAction.message_delete
            async for _log in guild.audit_logs(limit=2, action=action):
                if not _log.target:
                    continue
                same_chan = _log.extra.channel.id == message.channel.id
                if _log.target.id == message.author.id and same_chan:
                    perp = f"{_log.user}({_log.user.id})"
                    break
        message_channel = cast(discord.TextChannel, message.channel)
        author = message.author

        content = list(pagify(f"{message.author.mention}\n\n{message.content}", page_length=1000))
        embed = discord.Embed(description=content.pop(0), colour=await self.get_event_colour(guild, "message_delete"), timestamp=time)
        for more_content in content:
            embed.add_field(name="Message Continued", value=more_content)
        embed.add_field(name="Channel", value=str(message_channel))
        if perp:
            embed.add_field(name="Deleted by", value=perp)
        discord_files = []
        if message.attachments:
            for i in message.attachments:
                try:
                    f = await i.to_file(use_cached=True)
                except (discord.HTTPException, aiohttp.ClientError):
                    try:
                        f = await i.to_file()
                    except (discord.HTTPException, aiohttp.ClientError):
                        log.warning("Unable to fetch attachment {}", i.url)
                        f = None
                if f:
                    discord_files.append(f)
            files = ", ".join(a.filename for a in message.attachments)
            if len(message.attachments) > 1:
                files = files[:-2]
            embed.add_field(name="attachments", value=files)
        embed.set_author(name=f"{author} ({author.id}) deleted message", icon_url=str(message.author.avatar_url))
        if channel:
            with contextlib.suppress(discord.NotFound):
                await channel.send(embed=embed, files=discord_files)

    async def send_payload(self, channel_id: int, modlog_channel: discord.TextChannel):
        await checkpoint()
        await asyncio.sleep(8)
        await checkpoint()
        spawn_task(self.send_payload_final(channel_id, modlog_channel), self.active_tasks)

    async def send_payload_final(self, channel_id: int, modlog_channel: discord.TextChannel):
        payload: dict = await self.redis.json().get("bulk_del_queue", f"channels.{channel_id}")
        if not payload:
            return log.warning(f"Task queue was started but queue ended empty {channel_id}")
        users = []
        count = 0
        async with self.redis.json().pipeline() as pipe:
            for m in payload.values():
                mid = f"id_{m['id']}"
                pipe.delete("bulk_del_queue", f"channels.{channel_id}.{mid}")
                await checkpoint()
                if "mentions" in m:
                    del m["mentions"]
                if "embeds" in m and not m["embeds"]:
                    m["embeds"] = []
                count += 1
                if "username" not in m["author"]:
                    m["author"]["username"] = m["author"].get("name", "NA")
                username = m["author"]["username"]
                m["author"]["id"] = str(m["author"]["id"])
                disc = m["author"]["discriminator"]
                user = f"{username}#{disc}"
                user = user.replace("#0", "")
                if user not in users:
                    users.append(user)
            await pipe.execute()
        users_str = "".join(f"{u} " for u in users)
        users_str = users_str.replace("#0", "")
        payload = sorted(payload.values(), key=lambda x: int(x["id"]))
        payload = orjson.dumps(payload).decode("UTF-8", "ignore")
        url = "https://logs.melaniebot.net/api/v2/logs/"
        exp = arrow.utcnow().shift(months=10)
        params = {"type": "melanie", "messages": payload, "expires": str(exp)}
        headers = {"Authorization": "Token 03932779cfed7d213bbf35240eb8bc10771c01e5"}
        async with self.bot.aio.post(url, data=params, headers=headers, timeout=120) as r:
            r.raise_for_status()
            _data = await r.json()
            url = _data["url"]
        url = str(url).replace("http://", "https://")
        await self.config.custom("WebsiteLogs", modlog_channel.guild.id, url.split("/")[-1]).set({"created_at": int(time.time()), "size": int(count)})
        log.success("Created log url {}", url)
        embed = discord.Embed()
        embed.colour = 16382714
        embed.title = "bulk message delete"
        ch_o = self.bot.get_channel(channel_id)
        ch_str = str(ch_o) if ch_o else "deleted channel"
        embed.add_field(name="channel name", value=ch_str)
        embed.add_field(name="message count", value=count)
        short_users = textwrap.shorten(users_str, width=600, placeholder="...")
        embed.add_field(name="users", value=short_users)
        embed.add_field(name="log", value=url)
        embed.timestamp = arrow.now().datetime
        embed.set_footer(text="melanie | logs retained for 1 year!", icon_url=footer_gif)
        if modlog_channel:
            await modlog_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if not self.valid_event():
            return
        guild_id = payload.guild_id
        if guild_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild.id not in self.settings:
            return
        settings = self.settings[guild.id]["message_delete"]
        if not settings["enabled"] or not settings["bulk_enabled"]:
            return
        channel_id = payload.channel_id
        message_channel = guild.get_channel(channel_id)
        try:
            channel = await self.modlog_channel(guild, "message_delete")
        except RuntimeError:
            return
        if await self.is_ignored_channel(guild, message_channel):
            return
        redis = get_redis()
        found_mids = 0
        missing_ids = [str(i) for i in payload.message_ids]
        if active_task := self.bulk_submit_tasks.get(channel_id):
            active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)

        async with redis.json().pipeline() as pipe:
            pipe.set("bulk_del_queue", ".", {"channels": {}}, nx=True)
            pipe.set("bulk_del_queue", f".channels.{channel_id}", {}, nx=True)
            if missing_ids:
                exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
                if not exe.database:
                    return log.error("No DB")
                async with exe.database.acquire() as con:
                    con: asyncpg.Connection
                    async with con.transaction():
                        async for res in con.cursor(
                            "select * from guild_messages where message_id = any($1::text[])  and guild_id = $2",
                            list(missing_ids),
                            str(guild.id),
                        ):
                            found_mids += 1
                            data = MelanieMessage(**dict(res))
                            missing_ids.remove(data.message_id)
                            item = {
                                "id": int(data.message_id),
                                "author": {
                                    "id": str(data.user_id),
                                    "username": data.user_name,
                                    "discriminator": data.user_discrim,
                                    "avatar": data.user_avatar,
                                },
                                "content": data.content,
                                "timestamp": str(data.created_at),
                                "timestamp2": int(time.time()),
                            }
                            if data.embeds:
                                item["embeds"] = data.embeds
                            mid = f"id_{data.message_id}"
                            pipe.set("bulk_del_queue", f"channels.{channel_id}.{mid}", item)

            await pipe.execute()
        if found_mids:
            if active_task := self.bulk_submit_tasks.get(channel_id):
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
            self.bulk_submit_tasks[channel_id] = create_task(self.send_payload(channel_id, channel))

    async def invite_links_loop(self) -> None:
        """Check every 5 minutes for updates to the invite links."""
        await self.bot.wait_until_red_ready()
        if self.bot.user.name == "melanie":
            return

        while True:
            for guild_id in self.settings:
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                if self.settings[guild_id]["user_join"]["enabled"]:
                    await self.save_invite_links(guild)
            await asyncio.sleep(300)

    async def save_invite_links(self, guild: discord.Guild) -> bool:
        if self.bot.user.name == "melanie":
            return True
        invites = {}
        if not guild.me.guild_permissions.manage_guild:
            return False
        for invite in await guild.invites():
            try:
                created_at = getattr(invite, "created_at", datetime.datetime.utcnow())
                channel = getattr(invite, "channel", discord.Object(id=0))
                inviter = getattr(invite, "inviter", discord.Object(id=0))
                invites[invite.code] = {
                    "uses": getattr(invite, "uses", 0),
                    "max_age": getattr(invite, "max_age", None),
                    "created_at": created_at.timestamp(),
                    "max_uses": getattr(invite, "max_uses", None),
                    "temporary": getattr(invite, "temporary", False),
                    "inviter": getattr(inviter, "id", "Unknown"),
                    "channel": getattr(channel, "id", "Unknown"),
                }
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error saving invites.")
        await self.config.guild(guild).invite_links.set(invites)
        self.settings[guild.id]["invite_links"] = invites
        return True

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not self.valid_event():
            return
        guild = member.guild
        if guild.id not in self.settings:
            return
        if not self.settings[guild.id]["user_join"]["enabled"]:
            return

        try:
            channel = await self.modlog_channel(guild, "user_join")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        users = len(guild.members)
        #
        since_created = (time - member.created_at).days
        user_created = member.created_at.strftime("%d %b %Y %H:%M")

        created_on = f"{user_created}\n({since_created} days ago)"

        if embed_links:
            embed = discord.Embed(
                description=member.mention,
                colour=await self.get_event_colour(guild, "user_join"),
                timestamp=member.joined_at or datetime.datetime.utcnow(),
            )

            embed.add_field(name="Total Users:", value=str(users))
            embed.add_field(name="Account created on:", value=created_on)
            embed.set_author(name=f"{member} ({member.id}) has joined the guild", url=member.avatar_url, icon_url=member.avatar_url)

            embed.set_thumbnail(url=member.avatar_url)
            await channel.send(embed=embed)
        else:
            time = datetime.datetime.utcnow()
            msg = f"{self.settings[guild.id]['user_join']['emoji']} `{time.strftime('%H:%M:%S')}` **{member}**(`{member.id}`) joined the guild. Total members: {users}"
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member) -> None:
        """This is only used to track that the user was banned and not
        kicked/removed.
        """
        if not self.valid_event():
            return
        if guild.id not in self._ban_cache:
            self._ban_cache[guild.id] = [member.id]
        else:
            self._ban_cache[guild.id].append(member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not self.valid_event():
            return
        guild = member.guild
        await asyncio.sleep(2)
        if guild.id in self._ban_cache and member.id in self._ban_cache[guild.id]:
            # was a ban so we can leave early
            return
        if guild.id not in self.settings:
            return
        if not self.settings[guild.id]["user_left"]["enabled"]:
            return

        try:
            channel = await self.modlog_channel(guild, "user_left")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        perp, reason = await self.get_audit_log_reason(guild, member, discord.AuditLogAction.kick)
        if embed_links:
            embed = discord.Embed(description=member.mention, colour=await self.get_event_colour(guild, "user_left"), timestamp=time)
            embed.add_field(name="Total Users:", value=str(len(guild.members)))
            if perp:
                embed.add_field(name="Kicked", value=perp.mention)
            if reason:
                embed.add_field(name="Reason", value=str(reason), inline=False)
            embed.set_author(name=f"{member} ({member.id}) has left the guild", url=member.avatar_url, icon_url=member.avatar_url)
            embed.set_thumbnail(url=member.avatar_url)
            await channel.send(embed=embed)
        else:
            time = datetime.datetime.utcnow()
            msg = f"{self.settings[guild.id]['user_left']['emoji']} `{time.strftime('%H:%M:%S')}` **{member}**(`{member.id}`) left the guild. Total members: {len(guild.members)}"
            if perp:
                msg = f"{self.settings[guild.id]['user_left']['emoji']} `{time.strftime('%H:%M:%S')}` **{member}**(`{member.id}`) was kicked by {perp}. Total members: {len(guild.members)}"
            await channel.send(msg)

    async def get_permission_change(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel, embed_links: bool) -> str:
        guild = before.guild
        before_perms = {str(o.id): list(p) for o, p in before.overwrites.items()}
        after_perms = {str(o.id): list(p) for o, p in after.overwrites.items()}
        p_msg = ""
        for entity, value in before_perms.items():
            entity_obj = before.guild.get_role(int(entity)) or before.guild.get_member(int(entity))
            if not entity_obj or not entity_obj.name:
                continue

            name = entity_obj.mention if embed_links else entity_obj.name

            if entity not in after_perms:
                perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.overwrite_delete)
                if perp:
                    p_msg += f"{perp.mention if embed_links else perp.name} Removed overwrites.\n"
                p_msg += f"{name} Overwrites removed.\n"

                lost_perms = set(before_perms[entity])
                for diff in lost_perms:
                    if diff[1] is None:
                        continue
                    p_msg += f"{name} {diff[0]} Reset.\n"
                continue
            if after_perms[entity] != value:
                perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.overwrite_update)
                if perp:
                    p_msg += f"{perp.mention if embed_links else perp.name} Updated overwrites.\n"
                a = set(after_perms[entity])
                b = set(before_perms[entity])
                a_perms = list(a - b)
                for diff in a_perms:
                    p_msg += f"{name} {diff[0]} Set to {diff[1]}.\n"
        for entity in after_perms:
            entity_obj = after.guild.get_role(int(entity)) or after.guild.get_member(int(entity))
            if not entity_obj or not entity_obj.name:
                continue
            name = entity_obj.mention if embed_links else entity_obj.name
            if entity not in before_perms:
                perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.overwrite_update)
                if perp:
                    p_msg += f"{perp.mention if embed_links else perp.name} Added overwrites.\n"
                p_msg += f"{name} Overwrites added.\n"
                lost_perms = set(after_perms[entity])
                for diff in lost_perms:
                    if diff[1] is None:
                        continue
                    p_msg += f"{name} {diff[0]} Set to {diff[1]}.\n"
                continue
        return p_msg

    @commands.Cog.listener()
    async def on_guild_channel_create(self, new_channel: discord.abc.GuildChannel) -> None:
        if not self.valid_event():
            return
        guild = new_channel.guild
        if guild.id not in self.settings:
            return
        if not self.settings[guild.id]["channel_create"]["enabled"]:
            return

        if await self.is_ignored_channel(guild, new_channel):
            return
        try:
            channel = await self.modlog_channel(guild, "channel_create")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        channel_type = str(new_channel.type).title()
        embed = discord.Embed(
            description=f"{new_channel.mention} {new_channel.name}",
            timestamp=time,
            colour=await self.get_event_colour(guild, "channel_create"),
        )
        embed.set_author(name=f"{channel_type} Channel Created {new_channel.name} ({new_channel.id})")
        perp, reason = await self.get_audit_log_reason(guild, new_channel, discord.AuditLogAction.channel_create)

        perp_msg = ""
        embed.add_field(name="Type", value=channel_type)
        if perp:
            perp_msg = f"by {perp} (`{perp.id}`)"
            embed.add_field(name="Created by ", value=perp.mention)
        if reason:
            perp_msg += f" Reason: {reason}"
            embed.add_field(name="Reason ", value=reason, inline=False)
        msg = f"{self.settings[guild.id]['channel_create']['emoji']} `{time.strftime('%H:%M:%S')}` {channel_type} channel created {perp_msg} {new_channel.mention}"
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, old_channel: discord.abc.GuildChannel) -> None:
        if not self.valid_event():
            return
        guild = old_channel.guild
        if guild.id not in self.settings:
            return
        if not self.settings[guild.id]["channel_delete"]["enabled"]:
            return

        if await self.is_ignored_channel(guild, old_channel):
            return
        try:
            channel = await self.modlog_channel(guild, "channel_delete")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        channel_type = str(old_channel.type).title()
        time = datetime.datetime.utcnow()
        embed = discord.Embed(description=old_channel.name, timestamp=time, colour=await self.get_event_colour(guild, "channel_delete"))
        embed.set_author(name=f"{channel_type} Channel Deleted {old_channel.name} ({old_channel.id})")
        perp, reason = await self.get_audit_log_reason(guild, old_channel, discord.AuditLogAction.channel_delete)

        perp_msg = ""
        embed.add_field(name="Type", value=channel_type)
        if perp:
            perp_msg = f"by {perp} (`{perp.id}`)"
            embed.add_field(name="Deleted by ", value=perp.mention)
        if reason:
            perp_msg += f" Reason: {reason}"
            embed.add_field(name="Reason ", value=reason, inline=False)
        msg = ("{emoji} `{time}` {chan_type} channel deleted {perp_msg} {channel}").format(
            emoji=self.settings[guild.id]["channel_delete"]["emoji"],
            time=time.strftime("%H:%M:%S"),
            chan_type=channel_type,
            perp_msg=perp_msg,
            channel=f"#{old_channel.name} ({old_channel.id})",
        )
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    async def get_audit_log_reason(
        self,
        guild: discord.Guild,
        target: Union[discord.abc.GuildChannel, discord.Member, discord.Role],
        action: discord.AuditLogAction,
    ) -> tuple[Optional[discord.abc.User], Optional[str]]:
        if not self.valid_event():
            return
        perp = None
        reason = None
        if guild.me.guild_permissions.view_audit_log:
            async for log in guild.audit_logs(limit=5, action=action):
                if log.target.id == target.id:
                    perp = log.user
                    if log.reason:
                        reason = log.reason
                    break
        return perp, reason

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if not self.valid_event():
            return
        guild = before.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["channel_change"]["enabled"]:
            return
        if await self.is_ignored_channel(guild, before):
            return
        try:
            channel = await self.modlog_channel(guild, "channel_change")
        except RuntimeError:
            return
        embed_links = True
        channel_type = str(after.type).title()
        time = datetime.datetime.utcnow()
        embed = discord.Embed(description=after.mention, timestamp=time, colour=await self.get_event_colour(guild, "channel_change"))
        embed.set_author(name=f"{channel_type} Channel Updated {before.name} ({before.id})")
        msg = f"{self.settings[guild.id]['channel_change']['emoji']} `{time.strftime('%H:%M:%S')}` Updated channel {before.name}\n"
        worth_updating = False
        perp = None
        reason = None
        if isinstance(before, discord.TextChannel):
            text_updates = {"name": "Name:", "topic": "Topic:", "category": "Category:", "slowmode_delay": "Slowmode delay:"}

            for attr, name in text_updates.items():
                before_attr = getattr(before, attr)
                after_attr = getattr(after, attr)
                if before_attr != after_attr:
                    worth_updating = True
                    if before_attr == "":
                        before_attr = "None"
                    if after_attr == "":
                        after_attr = "None"
                    msg += f"Before {name} {before_attr}\n"
                    msg += f"After {name} {after_attr}\n"
                    embed.add_field(name=f"Before {name}", value=str(before_attr)[:1024])
                    embed.add_field(name=f"After {name}", value=str(after_attr)[:1024])
                    perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.channel_update)
            if before.is_nsfw() != after.is_nsfw():
                worth_updating = True
                msg += f"Before NSFW {before.is_nsfw()}\n"
                msg += f"After NSFW {after.is_nsfw()}\n"
                embed.add_field(name="Before " + "NSFW", value=str(before.is_nsfw()))
                embed.add_field(name="After " + "NSFW", value=str(after.is_nsfw()))
                perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.channel_update)
            p_msg = await self.get_permission_change(before, after, embed_links)
            if p_msg != "":
                worth_updating = True
                msg += f"Permissions Changed: {p_msg}"
                for page in pagify(p_msg, page_length=1024):
                    embed.add_field(name="Permissions", value=page)

        if isinstance(before, discord.VoiceChannel):
            voice_updates = {"name": "Name:", "position": "Position:", "category": "Category:", "bitrate": "Bitrate:", "user_limit": "User limit:"}
            for attr, name in voice_updates.items():
                before_attr = getattr(before, attr)
                after_attr = getattr(after, attr)
                if before_attr != after_attr:
                    worth_updating = True
                    msg += f"Before {name} {before_attr}\n"
                    msg += f"After {name} {after_attr}\n"
                    embed.add_field(name=f"Before {name}", value=str(before_attr))
                    embed.add_field(name=f"After {name}", value=str(after_attr))
            p_msg = await self.get_permission_change(before, after, embed_links)
            if p_msg != "":
                worth_updating = True
                msg += f"Permissions Changed: {p_msg}"
                for page in pagify(p_msg, page_length=1024):
                    embed.add_field(name="Permissions", value=page)

        if perp:
            msg += f"Updated by {str(perp)}" + "\n"
            embed.add_field(name="Updated by ", value=perp.mention)
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        if not worth_updating:
            return
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    async def get_role_permission_change(self, before: discord.Role, after: discord.Role) -> str:
        changed_perms = dict(after.permissions).items() - dict(before.permissions).items()

        return "".join(f"{p} Set to **{change}**\n" for p, change in changed_perms)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if not self.valid_event():
            return
        guild = before.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["role_change"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "role_change")
        except RuntimeError:
            return
        perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.role_update)
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        embed = discord.Embed(description=after.mention, colour=after.colour, timestamp=time)
        msg = f"{self.settings[guild.id]['role_change']['emoji']} `{time.strftime('%H:%M:%S')}` Updated role **{before.name}**\n"
        if after is guild.default_role:
            embed.set_author(name="Updated @everyone role ")
        else:
            embed.set_author(name=f"Updated {before.name} ({before.id}) role ")
        if perp:
            msg += f"Updated by {str(perp)}" + "\n"
            embed.add_field(name="Updated by ", value=perp.mention)
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        role_updates = {"name": "Name:", "color": "Colour:", "mentionable": "Mentionable:", "hoist": "Is Hoisted:"}
        worth_updating = False
        for attr, name in role_updates.items():
            before_attr = getattr(before, attr)
            after_attr = getattr(after, attr)
            if before_attr != after_attr:
                worth_updating = True
                if before_attr == "":
                    before_attr = "None"
                if after_attr == "":
                    after_attr = "None"
                msg += f"Before {name} {before_attr}\n"
                msg += f"After {name} {after_attr}\n"
                embed.add_field(name=f"Before {name}", value=str(before_attr))
                embed.add_field(name=f"After {name}", value=str(after_attr))
        p_msg = await self.get_role_permission_change(before, after)
        if p_msg != "":
            worth_updating = True
            msg += f"Permissions Changed: {p_msg}"
            embed.add_field(name="Permissions", value=p_msg[:1024])
        if not worth_updating:
            return
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        if not self.valid_event():
            return
        guild = role.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["role_create"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "role_create")
        except RuntimeError:
            return
        perp, reason = await self.get_audit_log_reason(guild, role, discord.AuditLogAction.role_create)
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        embed = discord.Embed(description=role.mention, colour=await self.get_event_colour(guild, "role_create"), timestamp=time)
        embed.set_author(name=f"Role created {role.name} ({role.id})")
        msg = f"{self.settings[guild.id]['role_create']['emoji']} `{time.strftime('%H:%M:%S')}` Role created {role.name}\n"
        if perp:
            embed.add_field(name="Created by", value=perp.mention)
            msg += f"By {str(perp)}" + "\n"
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if not self.valid_event():
            return
        guild = role.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["role_delete"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "role_delete")
        except RuntimeError:
            return
        perp, reason = await self.get_audit_log_reason(guild, role, discord.AuditLogAction.role_delete)
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        embed = discord.Embed(description=role.name, timestamp=time, colour=await self.get_event_colour(guild, "role_delete"))
        embed.set_author(name=f"Role deleted {role.name} ({role.id})")
        msg = f"{self.settings[guild.id]['role_delete']['emoji']} `{time.strftime('%H:%M:%S')}` Role deleted **{role.name}**\n"
        if perp:
            embed.add_field(name="Deleted by", value=perp.mention)
            msg += f"By {str(perp)}" + "\n"
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not self.valid_event():
            return
        guild = before.guild
        if guild is None:
            return
        if guild.id not in self.settings:
            return

        settings = self.settings[guild.id]["message_edit"]
        if not settings["enabled"]:
            return
        if before.author.bot and not settings["bots"]:
            return
        if before.content == after.content:
            return
        try:
            channel = await self.modlog_channel(guild, "message_edit")
        except RuntimeError:
            return
        if await self.is_ignored_channel(guild, after.channel):
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        if embed_links:
            embed = discord.Embed(
                description=f"{before.author.mention}: {before.content}",
                colour=await self.get_event_colour(guild, "message_edit"),
                timestamp=before.created_at,
            )
            jump_url = f"[Click to see new message]({after.jump_url})"
            embed.add_field(name="After Message:", value=jump_url)
            embed.add_field(name="Channel:", value=before.channel.mention)
            embed.set_author(name=f"{before.author} ({before.author.id}) - Edited Message", icon_url=str(before.author.avatar_url))
            await channel.send(embed=embed)
        else:
            fmt = "%H:%M:%S"
            msg = f"{self.settings[guild.id]['message_edit']['emoji']} `{time.strftime(fmt)}` **{before.author}** (`{before.author.id}`) edited a message in {before.channel.mention}.\nBefore:\n> {escape(before.content, mass_mentions=True)}\nAfter:\n> {escape(after.content, mass_mentions=True)}"
            await channel.send(msg[:2000])

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if not self.valid_event():
            return
        guild = after
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["guild_change"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "guild_change")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        embed = discord.Embed(timestamp=time, colour=await self.get_event_colour(guild, "guild_change"))
        embed.set_author(name="Updated Guild", icon_url=str(guild.icon_url))
        embed.set_thumbnail(url=str(guild.icon_url))
        msg = f"{self.settings[guild.id]['guild_change']['emoji']} `{time.strftime('%H:%M:%S')}` Guild updated\n"
        guild_updates = {
            "name": "Name:",
            "region": "Region:",
            "afk_timeout": "AFK Timeout:",
            "afk_channel": "AFK Channel:",
            "icon_url": "Server Icon:",
            "owner": "Server Owner:",
            "splash": "Splash Image:",
            "system_channel": "Welcome message channel:",
            "verification_level": "Verification Level:",
        }
        worth_updating = False
        for attr, name in guild_updates.items():
            before_attr = getattr(before, attr)
            after_attr = getattr(after, attr)
            if before_attr != after_attr:
                worth_updating = True
                if attr == "icon_url":
                    embed.description = "Server Icon Updated"
                    embed.set_image(url=after.icon_url)
                    continue
                msg += f"Before {name} {before_attr}\n"
                msg += f"After {name} {after_attr}\n"
                embed.add_field(name=f"Before {name}", value=str(before_attr))
                embed.add_field(name=f"After {name}", value=str(after_attr))
        if not worth_updating:
            return
        perps = []
        reasons = []
        action = discord.AuditLogAction.guild_update
        async for log in guild.audit_logs(limit=len(embed.fields) // 2, action=action):
            perps.append(log.user)
            if log.reason:
                reasons.append(log.reason)
        if perps:
            perp_s = ", ".join(str(p) for p in perps)
            msg += f"Update by {perp_s}\n"
            perp_m = ", ".join(p.mention for p in perps)
            embed.add_field(name="Updated by", value=perp_m)
        if reasons:
            s_reasons = ", ".join(str(r) for r in reasons)
            msg += f"Reasons {reasons}\n"
            embed.add_field(name="Reasons ", value=s_reasons, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before: Sequence[discord.Emoji], after: Sequence[discord.Emoji]) -> None:
        if not self.valid_event():
            return
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["emoji_change"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "emoji_change")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        perp = None

        time = datetime.datetime.utcnow()
        embed = discord.Embed(description="", timestamp=time, colour=await self.get_event_colour(guild, "emoji_change"))
        embed.set_author(name="Updated Server Emojis")
        msg = f"{self.settings[guild.id]['emoji_change']['emoji']} `{time.strftime('%H:%M:%S')}` Updated Server Emojis"
        worth_updating = False
        b = set(before)
        a = set(after)
        added_emoji: Optional[discord.Emoji] = None
        removed_emoji: Optional[discord.Emoji] = None
        # discord.Emoji uses id for hashing so we use set difference to get added/removed emoji
        with contextlib.suppress(KeyError):
            added_emoji = (a - b).pop()
        with contextlib.suppress(KeyError):
            removed_emoji = (b - a).pop()
        # changed emojis have their name and/or allowed roles changed while keeping id unchanged
        to_iter = (*before, added_emoji) if added_emoji is not None else before
        changed_emoji = {(e, e.name, tuple(e.roles)) for e in after}
        changed_emoji.difference_update((e, e.name, tuple(e.roles)) for e in to_iter)
        try:
            changed_emoji = changed_emoji.pop()[0]
        except KeyError:
            changed_emoji = None
        else:
            for old_emoji in before:
                if old_emoji.id == changed_emoji.id:
                    break
            else:
                # this shouldn't happen but it's here just in case
                changed_emoji = None
        action = None
        if removed_emoji is not None:
            worth_updating = True
            new_msg = f"`{removed_emoji}` (ID: {removed_emoji.id}) Removed from the guild\n"
            msg += new_msg
            embed.description += new_msg
            action = discord.AuditLogAction.emoji_delete
        elif added_emoji is not None:
            worth_updating = True
            new_emoji = f"{added_emoji} `{added_emoji}`"
            new_msg = f"{new_emoji} Added to the guild\n"
            msg += new_msg
            embed.description += new_msg
            action = discord.AuditLogAction.emoji_create
        elif changed_emoji is not None:
            worth_updating = True
            emoji_name = f"{changed_emoji} `{changed_emoji}`"
            if old_emoji.name != changed_emoji.name:
                new_msg = f"{emoji_name} Renamed from {old_emoji.name} to {changed_emoji.name}\n"
                # emoji_update shows only for renames and not for role restriction updates
                action = discord.AuditLogAction.emoji_update
                msg += new_msg
                embed.description += new_msg
            if old_emoji.roles != changed_emoji.roles:
                worth_updating = True
                if not changed_emoji.roles:
                    new_msg = f"{emoji_name} Changed to unrestricted.\n"
                elif not old_emoji.roles:
                    new_msg = ("{emoji} Restricted to roles: {roles}\n").format(
                        emoji=emoji_name,
                        roles=humanize_list([f"{role.name} ({role.id})" for role in changed_emoji.roles]),
                    )
                else:
                    new_msg = ("{emoji} Role restriction changed from\n {old_roles}\n To\n {new_roles}").format(
                        emoji=emoji_name,
                        old_roles=humanize_list([f"{role.mention} ({role.id})" for role in old_emoji.roles]),
                        new_roles=humanize_list([f"{role.name} ({role.id})" for role in changed_emoji.roles]),
                    )
                msg += new_msg
                embed.description += new_msg
        perp = None
        reason = None
        if not worth_updating:
            return
        if action:
            async for log in guild.audit_logs(limit=1, action=action):
                perp = log.user
                if log.reason:
                    reason = log.reason
                break
        if perp:
            embed.add_field(name="Updated by ", value=perp.mention)
            msg += f"Updated by {str(perp)}" + "\n"
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if not self.valid_event():
            return
        guild = member.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["voice_change"]["enabled"]:
            return
        if member.bot and not self.settings[guild.id]["voice_change"]["bots"]:
            return
        try:
            channel = await self.modlog_channel(guild, "voice_change")
        except RuntimeError:
            return
        if after.channel is not None and await self.is_ignored_channel(guild, after.channel):
            return
        if before.channel is not None and await self.is_ignored_channel(guild, before.channel):
            return
        embed_links = True

        # set guild level i18n
        time = datetime.datetime.utcnow()
        embed = discord.Embed(timestamp=time, colour=await self.get_event_colour(guild, "voice_change"))
        msg = f"{self.settings[guild.id]['voice_change']['emoji']} `{time.strftime('%H:%M:%S')}` Updated Voice State for **{member}** (`{member.id}`)"
        embed.set_author(name=f"{member} ({member.id}) Voice State Update")
        change_type = None
        worth_updating = False
        if before.deaf != after.deaf:
            worth_updating = True
            change_type = "deaf"
            chan_msg = f"{member.mention} was deafened. " if after.deaf else f"{member.mention} was undeafened. "
            msg += chan_msg + "\n"
            embed.description = chan_msg
        if before.mute != after.mute:
            worth_updating = True
            change_type = "mute"
            chan_msg = f"{member.mention} was muted." if after.mute else f"{member.mention} was unmuted. "
            msg += chan_msg + "\n"
            embed.description = chan_msg
        if before.channel != after.channel:
            worth_updating = True
            change_type = "channel"
            if before.channel is None:
                channel_name = f"`{after.channel.name}` ({after.channel.id}) {after.channel.mention}"
                chan_msg = f"{member.mention} has joined {channel_name}"
                msg += chan_msg + "\n"
            elif after.channel is None:
                channel_name = f"`{before.channel.name}` ({before.channel.id}) {before.channel.mention}"
                chan_msg = f"{member.mention} has left {channel_name}"
                msg += chan_msg + "\n"
            else:
                after_chan = f"`{after.channel.name}` ({after.channel.id}) {after.channel.mention}"
                before_chan = f"`{before.channel.name}` ({before.channel.id}) {before.channel.mention}"
                chan_msg = f"{member.mention} has moved from {before_chan} to {after_chan}"
                msg += chan_msg
            embed.description = chan_msg
        if not worth_updating:
            return
        perp = None
        reason = None
        if change_type:
            action = discord.AuditLogAction.member_update
            async for log in guild.audit_logs(limit=5, action=action):
                is_change = getattr(log.after, change_type, None)
                if log.target.id == member.id and is_change:
                    perp = log.user
                    if log.reason:
                        reason = log.reason
                    break
        if perp:
            embed.add_field(name="Updated by", value=perp.mention)
        if reason:
            msg += f"Reason {reason}" + "\n"
            embed.add_field(name="Reason ", value=reason, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.bot.is_ready():
            return
        if self.bot.user.name == "melanie":
            return
        if not self.valid_event():
            return

        guild = before.guild
        if guild.id not in self.settings:
            return

        redis = get_redis()
        if not self.settings[guild.id]["user_change"]["enabled"]:
            return
        if not self.settings[guild.id]["user_change"]["bots"] and after.bot:
            return
        try:
            channel = await self.modlog_channel(guild, "user_change")
        except RuntimeError:
            return
        embed_links = True

        time = datetime.datetime.utcnow()
        embed = discord.Embed(timestamp=time, colour=await self.get_event_colour(guild, "user_change"))
        msg = f"{self.settings[guild.id]['user_change']['emoji']} `{time.strftime('%H:%M:%S')}` Member updated **{before}** (`{before.id}`)\n"
        embed.description = ""
        emb_msg = f"{before} ({before.id}) updated"
        embed.set_author(name=emb_msg, icon_url=before.avatar_url)
        member_updates = {"nick": "Nickname:", "roles": "Roles:"}
        perp = None
        reason = None
        worth_sending = False
        for attr, name in member_updates.items():
            if attr == "nick" and not self.settings[guild.id]["user_change"]["nicknames"]:
                continue
            before_attr = getattr(before, attr)
            after_attr = getattr(after, attr)
            if after_attr and "afk" in str(after_attr):
                return

            if before_attr and "afk" in str(before_attr):
                return

            if before_attr != after_attr:
                if attr == "roles":
                    b = set(before.roles)
                    a = set(after.roles)
                    before_roles = list(b - a)
                    after_roles = list(a - b)
                    if before_roles:
                        for role in before_roles:
                            prekey = f"{after.id}{guild.id}{role.id}"
                            key = f"vanity_roleadd:{xxh32_hexdigest(prekey)}"
                            if await redis.get(key):
                                return
                            msg += f"{after.name} had the {role.name} role removed."
                            embed.description += f"{after.mention} had the {role.mention} role removed.\n"
                            worth_sending = True
                    if after_roles:
                        for role in after_roles:
                            prekey = f"{after.id}{guild.id}{role.id}"
                            key = f"vanity_roleadd:{xxh32_hexdigest(prekey)}"
                            if await redis.get(key):
                                return
                            msg += f"{after.name} had the {role.name} role applied."
                            embed.description += f"{after.mention} had the {role.mention} role applied.\n"
                            worth_sending = True
                    perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.member_role_update)
                else:
                    perp, reason = await self.get_audit_log_reason(guild, before, discord.AuditLogAction.member_update)
                    worth_sending = True
                    msg += f"Before {name} {before_attr}\n"
                    msg += f"After {name} {after_attr}\n"
                    embed.description = f"{after.mention} changed their nickname."
                    embed.add_field(name=f"Before {name}", value=str(before_attr)[:1024])
                    embed.add_field(name=f"After {name}", value=str(after_attr)[:1024])
        if not worth_sending:
            return
        if perp:
            msg += f"Updated by {perp}\n"
            embed.add_field(name="Updated by ", value=perp.mention)
        if reason:
            if "Auto nick cleaning (v2) enabled." in reason:
                return
            msg += f"Reason: {reason}\n"
            embed.add_field(name="Reason", value=reason, inline=False)
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(msg)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        """New in discord.py 1.3."""
        guild = invite.guild
        if not self.valid_event():
            return
        if guild.id not in self.settings:
            return

        if invite.code not in self.settings[guild.id]["invite_links"]:
            created_at = getattr(invite, "created_at", datetime.datetime.utcnow())
            inviter = getattr(invite, "inviter", discord.Object(id=0))
            channel = getattr(invite, "channel", discord.Object(id=0))
            self.settings[guild.id]["invite_links"][invite.code] = {
                "uses": getattr(invite, "uses", 0),
                "max_age": getattr(invite, "max_age", None),
                "created_at": created_at.timestamp(),
                "max_uses": getattr(invite, "max_uses", None),
                "temporary": getattr(invite, "temporary", False),
                "inviter": getattr(inviter, "id", "Unknown"),
                "channel": channel.id,
            }
            await self.config.guild(guild).invite_links.set(self.settings[guild.id]["invite_links"])
        if not self.settings[guild.id]["invite_created"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "invite_created")
        except RuntimeError:
            return
        embed_links = True

        # set guild level i18n
        invite_attrs = {
            "code": "Code:",
            "inviter": "Inviter:",
            "channel": "Channel:",
            "max_uses": "Max Uses:",
            "max_age": "Max Age:",
            "temporary": "Temporary:",
        }
        try:
            invite_time = invite.created_at.strftime("%H:%M:%S")
        except AttributeError:
            invite_time = datetime.datetime.utcnow().strftime("%H:%M:%S")
        msg = f"{self.settings[guild.id]['invite_created']['emoji']} `{invite_time}` Invite created "
        embed = discord.Embed(title="Invite Created", colour=await self.get_event_colour(guild, "invite_created"))
        worth_updating = False
        if getattr(invite, "inviter", None):
            embed.description = f"{invite.inviter.mention} created an invite for {invite.channel.mention}."
        for attr, name in invite_attrs.items():
            if before_attr := getattr(invite, attr):
                if attr == "max_age":
                    before_attr = humanize_timedelta(seconds=before_attr)
                worth_updating = True
                msg += f"{name} {before_attr}\n"
                embed.add_field(name=name, value=str(before_attr))
        if not worth_updating:
            return
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        """New in discord.py 1.3."""
        if not self.valid_event():
            return
        guild = invite.guild
        if guild.id not in self.settings:
            return

        if not self.settings[guild.id]["invite_deleted"]["enabled"]:
            return
        try:
            channel = await self.modlog_channel(guild, "invite_deleted")
        except RuntimeError:
            return
        embed_links = True
        # set guild level i18n
        invite_attrs = {
            "code": "Code: ",
            "inviter": "Inviter: ",
            "channel": "Channel: ",
            "max_uses": "Max Uses: ",
            "uses": "Used: ",
            "max_age": "Max Age:",
            "temporary": "Temporary:",
        }
        try:
            invite_time = invite.created_at.strftime("%H:%M:%S")
        except AttributeError:
            invite_time = datetime.datetime.utcnow().strftime("%H:%M:%S")
        msg = f"{self.settings[guild.id]['invite_deleted']['emoji']} `{invite_time}` Invite deleted "
        embed = discord.Embed(title="Invite Deleted", colour=await self.get_event_colour(guild, "invite_deleted"))
        if getattr(invite, "inviter", None):
            embed.description = f"{invite.inviter.mention} deleted or used up an invite for {invite.channel.mention}."
        worth_updating = False
        for attr, name in invite_attrs.items():
            if before_attr := getattr(invite, attr):
                if attr == "max_age":
                    before_attr = humanize_timedelta(seconds=before_attr)
                worth_updating = True
                msg += f"{name} {before_attr}\n"
                embed.add_field(name=name, value=str(before_attr))
        if not worth_updating:
            return
        if embed_links:
            await channel.send(embed=embed)
        else:
            await channel.send(escape(msg, mass_mentions=True))

    def valid_event(self) -> bool:
        return self.bot.user.name == "melanie2"
