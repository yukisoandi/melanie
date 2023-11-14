import asyncio
import copy
import io
import pickle
import random
import string
import time
from copy import copy
from datetime import datetime
from io import BytesIO
from typing import Any, Optional, Pattern, cast

import discord
import msgpack
import regex as re
from anyio import Path as AsyncPath
from discord.utils import find
from distributed.actor import Actor
from loguru import logger as log
from melaniebot import VersionInfo, version_info
from melaniebot.core import Config, commands, modlog
from melaniebot.core.bot import Melanie
from melaniebot.core.data_manager import cog_data_path
from melaniebot.core.utils.chat_formatting import escape, humanize_list
from PIL import Image, ImageSequence
from xxhash import xxh32_hexdigest

from melanie import aiter, checkpoint, download_file_url, get_redis, make_e
from retrigger.actor import search_regex2

from .converters import Trigger
from .message import ReTriggerMessage

RE_CTX: Pattern = re.compile(r"{([^}]+)\}")
RE_POS: Pattern = re.compile(r"{((\d+)[^.}]*(\.[^:}]+)?[^}]*)\}", flags=re.IGNORECASE)
LINK_REGEX: Pattern = re.compile(r"(http[s]?:\/\/[^\"\']*\.(?:png|jpg|jpeg|gif|mp3|mp4))", flags=re.IGNORECASE)
IMAGE_REGEX: Pattern = re.compile(r"(?:(?:https?):\/\/)?[\w\/\-?=%.]+\.(?:png|jpg|jpeg)+", flags=re.IGNORECASE)


class TriggerHandler:
    """Handles all processing of triggers."""

    config: Config
    bot: Melanie
    triggers: dict[int, list[Trigger]]
    trigger_timeout: int
    ALLOW_RESIZE: bool = True
    ALLOW_OCR: bool = True
    actor: Actor

    def __init__(self, *args) -> None:
        self.config: Config
        self.bot: Melanie
        self.triggers: dict[int, list[Trigger]]
        self.trigger_timeout: int
        self.ALLOW_RESIZE = True
        self.ALLOW_OCR = True
        self.check_locks: dict[asyncio.Lock]

        self.trigger_cache: dict[asyncio.Lock]

    async def remove_trigger_from_cache(self, guild_id: int, trigger: Trigger) -> None:
        try:
            for t in self.triggers[guild_id]:
                if t.name == trigger.name:
                    self.triggers[guild_id].remove(t)
        except ValueError:
            log.info("Trigger can't be removed")

    async def can_edit(self, author: discord.Member, trigger: Trigger) -> bool:
        """Chekcs to see if the member is allowed to edit the trigger."""
        if trigger.author == author.id:
            return True
        return True if await self.bot.is_owner(author) else True

    async def check_bw_list(self, trigger: Trigger, message: discord.Message) -> bool:
        can_run = True
        author: discord.Member = cast(discord.Member, message.author)
        channel: discord.TextChannel = cast(discord.TextChannel, message.channel)
        if trigger.whitelist:
            can_run = False
            if channel.id in trigger.whitelist:
                can_run = True
            if channel.category_id and channel.category_id in trigger.whitelist:
                can_run = True
            if message.author.id in trigger.whitelist:
                can_run = True
            for role in author.roles:
                if role.is_default():
                    continue
                if role.id in trigger.whitelist:
                    can_run = True
            return can_run
        else:
            if channel.id in trigger.blacklist:
                can_run = False
            if channel.category_id and channel.category_id in trigger.blacklist:
                can_run = False
            if message.author.id in trigger.blacklist:
                can_run = False
            for role in author.roles:
                if role.is_default():
                    continue
                if role.id in trigger.blacklist:
                    can_run = False
        return can_run

    async def is_mod_or_admin(self, member: discord.Member) -> bool:
        guild = member.guild
        if member == guild.owner:
            return True
        if await self.bot.is_owner(member):
            return True
        if await self.bot.is_admin(member):
            return True
        return bool(await self.bot.is_mod(member))

    async def make_guild_folder(self, directory) -> None:
        if not directory.is_dir():
            log.info("Creating guild folder")
            directory.mkdir(exist_ok=True, parents=True)

    async def save_image_location(self, image_url: str, guild: discord.Guild) -> Optional[str]:
        good_image_url = LINK_REGEX.search(image_url)
        if not good_image_url:
            return None
        seed = "".join(random.sample(string.ascii_uppercase + string.digits, k=5))
        filename = good_image_url.group(1).split("/")[-1]
        filename = f"{seed}-{filename}"
        directory = cog_data_path(self) / str(guild.id)
        file_path = f"{str(cog_data_path(self))}/{guild.id}/{filename}"
        await self.make_guild_folder(directory)
        await download_file_url(good_image_url.group(1), file_path)
        return filename

    async def wait_for_image(self, ctx: commands.Context) -> Optional[discord.Message]:
        await ctx.send("Upload an image for me to use! Type `exit` to cancel.")
        msg = None
        while msg is None:

            def check(m):
                return m.author == ctx.author and (m.attachments or "exit" in m.content)

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=60)
            except TimeoutError:
                await ctx.send("Image adding timed out.")
                break
            if "exit" in msg.content.lower():
                await ctx.send("Image adding cancelled.")
                break
        return msg

    async def wait_for_multiple_images(self, ctx: commands.Context) -> list[str]:
        await ctx.send("Upload an image for me to use! Type `exit` to cancel.")
        files: list = []
        while True:

            def check(m):
                return m.author == ctx.author

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=60)
            except TimeoutError:
                return files
            if "exit" in msg.content.lower():
                return files
            link = LINK_REGEX.search(msg.content)
            for a in msg.attachments:
                if a.size > 8 * 1000 * 1000:
                    continue
                files.append(await self.save_image_location(a.url, ctx.guild))
                await msg.add_reaction("✅")
            if link:
                files.append(await self.save_image_location(link.group(0), ctx.guild))
                await msg.add_reaction("✅")
        return files

    async def wait_for_multiple_responses(self, ctx: commands.Context) -> list[discord.Message]:
        msg_text = "Please enter your desired phrase to be used for this trigger.Type `exit` to stop adding responses."
        await ctx.send(msg_text)
        responses: list = []
        while True:

            def check(m):
                return m.author == ctx.author

            try:
                message = await self.bot.wait_for("message", check=check, timeout=60)
                await message.add_reaction("✅")
            except TimeoutError:
                return responses
            if message.content == "exit":
                return responses
            else:
                responses.append(message.content)

    def resize_image(self, size: int, image: str) -> discord.File:
        length, width = (16, 16)  # Start with the smallest size we want to upload
        with Image.open(image) as im:
            if size <= 0:
                size = 1
            im.thumbnail((length * size, width * size))
            byte_array = BytesIO()
            im.save(byte_array, format="PNG")
            byte_array.seek(0)
            return discord.File(byte_array, filename="resize.png")

    def resize_gif(self, size: int, image: str) -> discord.File:
        img_list = []
        with Image.open(image) as im:
            if size <= 0:
                size = 1
            length, width = (16 * size, 16 * size)
            start_list = [frame.copy() for frame in ImageSequence.Iterator(im)]
            for frame in start_list:
                frame.thumbnail((length, width))
                img_list.append(frame)
        byte_array = BytesIO()
        img_list[0].save(byte_array, format="GIF", save_all=True, append_images=img_list, duration=0, loop=0)
        byte_array.seek(0)
        return discord.File(byte_array, filename="resize.gif")

    async def check_trigger_cooldown(self, message: discord.Message, trigger: Trigger) -> bool:
        now = datetime.now().timestamp()
        if trigger.cooldown:
            if trigger.cooldown["style"] in ["guild", "server"]:
                last = trigger.cooldown["last"]
                time = trigger.cooldown["time"]
                if now - last <= time:
                    return True
                trigger.cooldown["last"] = now
                return False
            else:
                style = trigger.cooldown["style"]
                snowflake = getattr(message, style)
                if snowflake.id not in [x["id"] for x in trigger.cooldown["last"]]:
                    trigger.cooldown["last"].append({"id": snowflake.id, "last": now})
                    return False
                else:
                    entity_list = trigger.cooldown["last"]
                    for entity in entity_list:
                        if entity["id"] == snowflake.id:
                            last = entity["last"]
                            time = trigger.cooldown["time"]
                            if (now - last) > time:
                                trigger.cooldown["last"].remove({"id": snowflake.id, "last": last})
                                trigger.cooldown["last"].append({"id": snowflake.id, "last": now})
                                return False
                            else:
                                return True
        return False

    async def check_is_command(self, message: discord.Message) -> bool:
        """Checks if the message is a bot command."""
        prefix_list = await self.bot.command_prefix(self.bot, message)
        msg = message.content
        is_command = False
        for prefix in prefix_list:
            if msg.startswith(prefix):
                # Don't run a trigger if it's the name of a command
                command_text = msg.replace(prefix, "").split(" ")[0]
                if not command_text:
                    continue
                command = self.bot.get_command(command_text)
                if command:
                    is_command = True
        return is_command

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.author.bot:
            return

        if getattr(message, "retrigger", False):
            log.debug("A ReTrigger dispatched message, ignoring.")
            return

        await asyncio.sleep(0.001)
        await self.check_triggers(message, False)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if "content" not in payload.data:
            return
        if "guild_id" not in payload.data:
            return
        guild = self.bot.get_guild(int(payload.data["guild_id"]))
        if not guild:
            return
        if guild.id not in self.triggers:
            return

        if not any(t.check_edits for t in self.triggers[guild.id]):
            return
        if "bot" in payload.data["author"]:
            return
        channel = guild.get_channel(int(payload.data["channel_id"]))
        message = await aiter(self.bot.cached_messages).find(lambda x: x.id == int(payload.data["id"]), None)
        if not message:
            try:
                message = find(lambda x: x.id == int(payload.data["id"]), self.bot.cached_messages) or await channel.fetch_message(int(payload.data["id"]))
            except (discord.errors.Forbidden, discord.errors.NotFound):
                log.debug("I don't have permission to read channel history or cannot find the message.")
                return

        if message.author.bot:
            # somehow we got a bot through the previous check :thonk:
            return
        await self.check_triggers(message, True)

    async def check_triggers(self, message: discord.Message, edit: bool) -> None:
        """This is where we iterate through the triggers and perform the search.

        This does all the permission checks and cooldown checks before
        actually running the regex to avoid possibly long regex
        operations.

        """
        guild: discord.Guild = message.guild
        if not guild:
            return
        if guild.id not in self.triggers:
            return
        channel: discord.TextChannel = cast(discord.TextChannel, message.channel)
        author: Optional[discord.Member] = guild.get_member(message.author.id)
        if not author:
            return
        blocked = not await self.bot.allowed_by_whitelist_blacklist(author)
        channel_perms = channel.permissions_for(author)
        is_command = await self.check_is_command(message)
        is_mod = await self.is_mod_or_admin(author)
        autoimmune = getattr(self.bot, "is_automod_immune", None)
        auto_mod = ["delete", "kick", "ban", "add_role", "remove_role"]
        checked = []
        for trigger in self.triggers[guild.id]:
            await checkpoint()
            if trigger.name in checked:
                continue
            checked.append(trigger.name)
            if not trigger.enabled:
                continue
            if edit and not trigger.check_edits:
                continue
            if trigger.chance and random.randint(0, trigger.chance) != 0:
                continue
            allowed_trigger = await self.check_bw_list(trigger, message)
            is_auto_mod = trigger.response_type in auto_mod
            if not allowed_trigger:
                continue
            if is_auto_mod and is_mod:
                continue
            if is_command and not trigger.ignore_commands:
                continue
            if any(t for t in trigger.response_type if t in auto_mod) and await autoimmune(message):
                continue
            if "delete" in trigger.response_type:
                if channel_perms.manage_messages or is_mod:
                    continue
            elif "kick" in trigger.response_type:
                if channel_perms.kick_members or is_mod:
                    continue
            elif "ban" in trigger.response_type:
                if channel_perms.ban_members or is_mod:
                    continue
            elif any(t for t in trigger.response_type if t in ["add_role", "remove_role"]):
                pass
            elif blocked:
                continue
            content = message.content
            if trigger.read_filenames and message.attachments:
                content = f"{message.content} " + " ".join(f.filename for f in message.attachments)

            hit = await self.search_and_run(message, trigger, content)
            if hit:
                return
            else:
                continue

    async def search_and_run(self, message: discord.Message, trigger: Trigger, content: str) -> None:
        search = await self.safe_regex_search(message.guild, trigger, content)
        if not search[0]:
            return
        elif search[1] != []:
            if await self.check_trigger_cooldown(message, trigger):
                return
            trigger.count += 1
            await self.perform_trigger(message, trigger, search[1])
            return search

    async def safe_regex_search(self, guild: discord.Guild, trigger: Trigger, content: str) -> tuple[bool, list]:
        """Mostly safe regex search to prevent reDOS from user defined regex
        patterns.

        This works by running the regex pattern inside a process pool
        defined at the cog level and then checking that process in the
        default executor to keep things asynchronous. If the process
        takes too long to complete we log a warning and remove the
        trigger from trying to run again.

        """
        if self.closed:
            log.warning("Closed")
            return (False, [])

        try:
            key = f"regex:{xxh32_hexdigest(f'{trigger.regex_raw}:{content}')}"
            async with self.check_locks[key]:
                if key not in self.trigger_cache:
                    task = self.bot.dask.submit(search_regex2, trigger.regex_raw, content, pure=True)
                    async with asyncio.timeout(10):
                        self.trigger_cache[key] = await task

            search = self.trigger_cache[key]
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self.bot.dask.cancel(task, force=True)
            error_msg = f"ReTrigger: regex asyncio timed out.{guild.name} ({guild.id}) Author {trigger.author} Offending regex `{trigger.regex.pattern}` Name: {trigger.name}"
            log.error(error_msg)
            return (False, [])
        except ValueError:
            log.error("Trigger value error {} {}", trigger.name, guild)
            return (False, [])
        else:
            return (True, search)

    async def load_file(self, path: str) -> bytes:
        _file = AsyncPath(path)
        if not await _file.exists():
            msg = f"File at path {path} not found"
            raise FileNotFoundError(msg)
        return await _file.read_bytes()

    async def perform_trigger(self, message: discord.Message, trigger: Trigger, find: list[str]) -> None:  # sourcery no-metrics
        async with self.guild_trigger_lock[message.guild.id]:
            guild: discord.Guild = cast(discord.Guild, message.guild)
            channel: discord.TextChannel = cast(discord.TextChannel, message.channel)
            author: discord.Member = cast(discord.Member, message.author)
            reason = f"Trigger response: {trigger.name}"
            own_permissions = channel.permissions_for(guild.me)
            error = None
            try:
                if "rename" in trigger.response_type and own_permissions.manage_nicknames:
                    # rename above text so the mention shows the renamed user name
                    if author == guild.owner:
                        # Don't want to accidentally kick the bot owner
                        # or try to kick the guild owner
                        return
                    if guild.me.top_role > author.top_role:
                        text_response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "rename") if trigger.multi_payload else str(trigger.text)
                        response = await self.convert_parms(message, text_response, trigger, find)
                        if response and not channel.permissions_for(author).mention_everyone:
                            response = escape(response, mass_mentions=True)
                        try:
                            await author.edit(nick=response[:32], reason=reason)
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "publish" in trigger.response_type and own_permissions.manage_messages and channel.is_news():
                    await message.publish()

                if "text" in trigger.response_type and own_permissions.send_messages:
                    text_response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "text") if trigger.multi_payload else str(trigger.text)
                    response = await self.convert_parms(message, text_response, trigger, find)
                    if response and not channel.permissions_for(author).mention_everyone:
                        response = escape(response, mass_mentions=True)
                    if version_info >= VersionInfo.from_str("3.4.6") and trigger.reply:
                        try:
                            await channel.send(
                                response,
                                tts=trigger.tts,
                                delete_after=trigger.delete_after,
                                reference=message,
                                allowed_mentions=trigger.allowed_mentions(),
                            )
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                    else:
                        try:
                            await channel.send(response, tts=trigger.tts, delete_after=trigger.delete_after, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "randtext" in trigger.response_type and own_permissions.send_messages:
                    rand_text_response: str = random.choice(trigger.text)
                    crand_text_response = await self.convert_parms(message, rand_text_response, trigger, find)
                    if crand_text_response and not channel.permissions_for(author).mention_everyone:
                        crand_text_response = escape(crand_text_response, mass_mentions=True)
                    if version_info >= VersionInfo.from_str("3.4.6") and trigger.reply is not None:
                        try:
                            await channel.send(crand_text_response, tts=trigger.tts, reference=message, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                    else:
                        try:
                            await channel.send(crand_text_response, tts=trigger.tts, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "image" in trigger.response_type and own_permissions.attach_files:
                    path = f"{str(cog_data_path(self))}/{guild.id}/{trigger.image}"
                    try:
                        file_data = await self.load_file(path)
                        file = discord.File(io.BytesIO(file_data), filename=trigger.image)
                    except FileNotFoundError:
                        redis = get_redis()
                        _key = f'disabled_trigger{xxh32_hexdigest(f"{trigger.name}{trigger.regex}{message.channel.id}")}'
                        if not await redis.get(_key):
                            await message.channel.send(
                                embed=make_e(f"The file for the trigger {trigger.name} was not found. This trigger will be disabled.", 2),
                            )
                            await redis.set(_key, time.time())
                        trigger.enabled = False
                        return log.warning(f"Missing file for {trigger}. Disabled trigger")
                    image_text_response = trigger.text
                    if image_text_response:
                        image_text_response = await self.convert_parms(message, image_text_response, trigger, find)
                    if image_text_response and not channel.permissions_for(author).mention_everyone:
                        image_text_response = escape(image_text_response, mass_mentions=True)
                    if version_info >= VersionInfo.from_str("3.4.6") and trigger.reply is not None:
                        try:
                            await channel.send(image_text_response, tts=trigger.tts, file=file, reference=message, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            trigger.enabled = False
                            raise e from e

                if "randimage" in trigger.response_type and own_permissions.attach_files:
                    image = random.choice(trigger.image)
                    path = f"{str(cog_data_path(self))}/{guild.id}/{image}"
                    file = discord.File(path)
                    rimage_text_response = trigger.text
                    if rimage_text_response:
                        rimage_text_response = await self.convert_parms(message, rimage_text_response, trigger, find)

                    if rimage_text_response and not channel.permissions_for(author).mention_everyone:
                        rimage_text_response = escape(rimage_text_response, mass_mentions=True)
                    if version_info >= VersionInfo.from_str("3.4.6") and trigger.reply is not None:
                        try:
                            await channel.send(rimage_text_response, tts=trigger.tts, file=file, reference=message, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                    else:
                        try:
                            await channel.send(rimage_text_response, tts=trigger.tts, file=file, allowed_mentions=trigger.allowed_mentions())
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "react" in trigger.response_type and own_permissions.add_reactions:
                    react_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "react"] if trigger.multi_payload else trigger.text
                    for emoji in react_response:
                        try:
                            await message.add_reaction(emoji)
                        except (discord.errors.Forbidden, discord.errors.NotFound):
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "add_role" in trigger.response_type and own_permissions.manage_roles:
                    add_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "add_role"] if trigger.multi_payload else trigger.text
                    for roles in add_response:
                        add_role: discord.Role = cast(discord.Role, guild.get_role(roles))
                        if not add_role:
                            continue
                        try:
                            await author.add_roles(add_role, reason=reason)
                            if await self.config.guild(guild).add_role_logs():
                                await self.modlog_action(message, trigger, find, "Added Role")
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "remove_role" in trigger.response_type and own_permissions.manage_roles:
                    rem_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "remove_role"] if trigger.multi_payload else trigger.text
                    for roles in rem_response:
                        rem_role: discord.Role = cast(discord.Role, guild.get_role(roles))
                        if not rem_role:
                            continue
                        try:
                            await author.remove_roles(rem_role, reason=reason)
                            if await self.config.guild(guild).remove_role_logs():
                                await self.modlog_action(message, trigger, find, "Removed Role")
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "kick" in trigger.response_type and own_permissions.kick_members:
                    if await self.bot.is_owner(author) or author == guild.owner:
                        # Don't want to accidentally kick the bot owner
                        # or try to kick the guild owner
                        return
                    if guild.me.top_role > author.top_role:
                        try:
                            await author.kick(reason=reason)
                            if await self.config.guild(guild).kick_logs():
                                await self.modlog_action(message, trigger, find, "Kicked")
                        except discord.errors.Forbidden:
                            log.debug("Retrigger encountered an error in {} with trigger {}", guild, trigger)

                if "ban" in trigger.response_type and own_permissions.ban_members:
                    return
                if "command" in trigger.response_type:
                    if trigger.multi_payload:
                        command_response = [t[1] for t in trigger.multi_payload if t[0] == "command"]
                        for command in command_response:
                            command = await self.convert_parms(message, command, trigger, find)
                            msg = copy(message)
                            prefix_list = await self.bot.command_prefix(self.bot, message)
                            msg.content = prefix_list[0] + command
                            msg = ReTriggerMessage(message=msg)
                            self.bot.dispatch("message", msg)
                    else:
                        msg = copy(message)
                        command = await self.convert_parms(message, str(trigger.text), trigger, find)
                        prefix_list = await self.bot.command_prefix(self.bot, message)
                        msg.content = prefix_list[0] + command
                        msg = ReTriggerMessage(message=msg)
                        self.bot.dispatch("message", msg)
                if "mock" in trigger.response_type:
                    if trigger.multi_payload:
                        mock_response = [t[1] for t in trigger.multi_payload if t[0] == "mock"]
                        for command in mock_response:
                            command = await self.convert_parms(message, command, trigger, find)
                            msg = copy(message)
                            mocker = guild.get_member(trigger.author)
                            if not mocker:
                                return
                            msg.author = mocker
                            prefix_list = await self.bot.command_prefix(self.bot, message)
                            msg.content = prefix_list[0] + command
                            msg = ReTriggerMessage(message=msg)
                            self.bot.dispatch("message", msg)
                    else:
                        msg = copy(message)
                        mocker = guild.get_member(trigger.author)
                        command = await self.convert_parms(message, str(trigger.text), trigger, find)
                        if not mocker:
                            return  # We'll exit early if the author isn't on the server anymore
                        msg.author = mocker
                        prefix_list = await self.bot.command_prefix(self.bot, message)
                        msg.content = prefix_list[0] + command
                        msg = ReTriggerMessage(message=msg)
                        self.bot.dispatch("message", msg)

                if "delete" in trigger.response_type and own_permissions.manage_messages:
                    # this should be last since we can accidentally delete the context when needed
                    log.debug("Performing delete trigger")
                    try:
                        await message.delete()
                        if await self.config.guild(guild).filter_logs():
                            await self.modlog_action(message, trigger, find, "Deleted Message")
                    except discord.errors.NotFound:
                        log.debug("Retrigger encountered an error in %r with trigger %r", guild, trigger)
                    except discord.errors.Forbidden:
                        log.debug("Retrigger encountered an error in %r with trigger %r", guild, trigger)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error = e
                raise error from error
            finally:
                await self.track_trigger_run(message, trigger, error)

    async def track_trigger_run(self, message: discord.Message, trigger: Trigger, error: Optional[Exception]):
        r = get_redis()
        if error:
            trigger.disable()
            log.warning("Disabled trigger {} @ {}", trigger.name, message.guild)
            try:
                error = pickle.dumps(error)
            except pickle.PicklingError:
                error = str(error)

        record = {
            "name": trigger.name,
            "error": error,
            "timestamp": time.time(),
            "regex": trigger.regex_raw,
            "channel_id": int(message.channel.id),
            "message_id": message.id,
        }
        await r.set(f"last_trigger:{message.channel.id}", msgpack.packb(record))

    async def convert_parms(self, message: discord.Message, raw_response: str, trigger: Trigger, find: list[str]) -> str:
        #
        results = RE_CTX.findall(raw_response)
        for result in results:
            param = await self.transform_parameter(result, message)
            raw_response = raw_response.replace("{" + result + "}", param)
        if results := RE_POS.findall(raw_response):
            for result in results:
                content = message.content
                if trigger.read_filenames and message.attachments:
                    content = f"{message.content} " + " ".join(f.filename for f in message.attachments)

                search = trigger.regex.search(content)
                if not search:
                    continue
                try:
                    arg = search.group(int(result[0]))
                    raw_response = raw_response.replace("{" + result[0] + "}", arg)
                except IndexError:
                    log.exception("Regex pattern is too broad and no matched groups were found.")
                    continue

        raw_response = raw_response.replace("{count}", str(trigger.count))
        if hasattr(message.channel, "guild"):
            prefixes = await self.bot.get_prefix(message.channel)
            raw_response = raw_response.replace("{p}", prefixes[0])
            raw_response = raw_response.replace("{pp}", humanize_list(prefixes))
            raw_response = raw_response.replace("{nummatch}", str(len(find)))
            raw_response = raw_response.replace("{lenmatch}", str(len(max(find))))
            raw_response = raw_response.replace("{lenmessage}", str(len(message.content)))
        return raw_response

    @staticmethod
    async def transform_parameter(result: str, message: discord.Message) -> str:
        """For security reasons only specific objects are allowed Internals are
        ignored.
        """
        raw_result = "{" + result + "}"
        objects: dict[str, Any] = {"message": message, "author": message.author, "channel": message.channel, "guild": message.guild, "server": message.guild}
        if message.attachments:
            objects["attachment"] = message.attachments[0]
            # we can only reasonably support one attachment at a time
        if result in objects:
            return str(objects[result])
        try:
            first, second = result.split(".")
        except ValueError:
            return raw_result
        if first in objects and not second.startswith("_"):
            first = objects[first]
        else:
            return raw_result
        return str(getattr(first, second, raw_result))

    async def modlog_action(self, message: discord.Message, trigger: Trigger, find: list[str], action: str) -> None:
        modlogs = await self.config.guild(message.guild).modlog()
        guild: discord.Guild = cast(discord.Guild, message.guild)
        author: discord.Member = cast(discord.Member, message.author)
        channel: discord.TextChannel = cast(discord.TextChannel, message.channel)
        if modlogs:
            if modlogs == "default":
                # We'll get the default modlog channel setup
                # with modlogset
                try:
                    modlog_channel = await modlog.get_modlog_channel(guild)
                except RuntimeError:
                    log.debug("Error getting modlog channel")
                    # Return early if no modlog channel exists
                    return
            else:
                modlog_channel = guild.get_channel(modlogs)
                if modlog_channel is None:
                    return
            infomessage = f"{author} - {action}\n"
            embed = discord.Embed(description=message.content, colour=discord.Colour.dark_red(), timestamp=datetime.now())
            found_regex = humanize_list(find)
            embed.add_field(name="Channel", value=channel.mention)
            embed.add_field(name="Trigger Name", value=trigger.name)
            if found_regex:
                embed.add_field(name="Found Triggers", value=found_regex[:1024])
            embed.add_field(name="Trigger author", value=f"<@{trigger.author}>")
            if message.attachments:
                files = ", ".join(a.filename for a in message.attachments)
                embed.add_field(name="Attachments", value=files)
            embed.set_footer(text=f"User ID: {str(message.author.id)}")
            embed.set_author(name=infomessage, icon_url=author.avatar_url)

            if modlog_channel.permissions_for(guild.me).embed_links:
                await modlog_channel.send(embed=embed)
            else:
                infomessage += ("Channel: {channel}\nTrigger Name: {trigger}\nTrigger author: {t_author}\nFound Triggers: {found_triggers}\n").format(
                    channel=channel.mention,
                    trigger=trigger.name,
                    t_author=f"{trigger.author}",
                    found_triggers=humanize_list(find)[:1024],
                )
                msg = escape(infomessage.replace("@&", ""), mass_mentions=True, formatting=True)
                await modlog_channel.send(msg)
