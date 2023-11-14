import asyncio
from collections import defaultdict
from typing import Optional

import discord
import regex as re
from aiomisc.periodic import PeriodicCallback
from anyio import Path as AsyncPath
from boltons.cacheutils import LRI
from distributed.actor import Actor
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.commands import TimedeltaConverter
from melaniebot.core.data_manager import cog_data_path
from melaniebot.core.utils.chat_formatting import humanize_list
from melaniebot.core.utils.menus import start_adding_reactions
from melaniebot.core.utils.predicates import ReactionPredicate
from xxhash import xxh32_hexdigest

from melanie import (
    cancel_tasks,
    capturetime,
    create_task,
    default_lock_cache,
    make_e,
    yesno,
)
from retrigger.converters import (
    ChannelUserRole,
    MultiResponse,
    Trigger,
    TriggerActionRecord,
    TriggerExists,
    ValidEmoji,
    ValidRegex,
)
from retrigger.menus import ReTriggerMenu, ReTriggerPages
from retrigger.triggerhandler import TriggerHandler


class ReTrigger(TriggerHandler, commands.Cog):
    """Trigger bot events using regular expressions."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.trigger_saves = defaultdict(asyncio.Lock)
        self.check_locks = defaultdict(asyncio.Lock)
        self.trigger_cache = LRI(120)
        self.guild_trigger_lock = defaultdict(asyncio.Lock)

        self.config = Config.get_conf(self, 964565433247, force_registration=True)
        default_guild = {
            "trigger_list": {},
            "allow_multiple": True,
            "modlog": "default",
            "ban_logs": False,
            "kick_logs": False,
            "add_role_logs": False,
            "remove_role_logs": False,
            "filter_logs": False,
            "bypass": False,
        }
        self.config.register_guild(**default_guild)
        self.find_locks = defaultdict(asyncio.Lock)
        self.config.register_global(trigger_timeout=1)
        self.locks = default_lock_cache()
        self.triggers: dict[int, list[Trigger]] = {}
        self.closed = False
        self.trigger_timeout = 1
        self.startup_event = asyncio.Event()
        self.prestart_event = asyncio.Event()
        self.active_tasks = [create_task(self.load_trigger_cache())]
        self.trigger_loop = PeriodicCallback(self.save_all_triggers)
        self.trigger_loop.start(45, delay=2)
        self.actor: Actor = None

    def cog_unload(self) -> None:
        self.closed = True
        self.trigger_loop.stop(True)
        if self.actor:
            create_task(self.bot.dask.cancel(self.actor._future, force=True))
        cancel_tasks(self.active_tasks)
        log.success("Executor shutdown OK")

    async def remove_trigger(self, guild_id: int, trigger_name: str) -> bool:
        """Returns true or false if the trigger was removed."""
        with log.catch(reraise=True):
            guild = self.bot.get_guild(guild_id)
            new_triggers = []
            async with self.trigger_saves[guild_id]:
                for trigger in self.triggers[guild_id]:
                    if trigger.name == trigger_name:
                        if trigger.image:
                            if isinstance(trigger.image, list):
                                for i in trigger.image:
                                    path = AsyncPath(f"{str(cog_data_path(self))}/{guild_id}/{i}")
                                    await path.unlink(missing_ok=True)
                            else:
                                path = AsyncPath(f"{str(cog_data_path(self))}/{guild_id}/{trigger.image}")
                                await path.unlink(missing_ok=True)
                    else:
                        new_triggers.append(trigger)
            self.triggers[guild.id] = new_triggers
            await self.save_all_triggers(guild)

    async def save_all_triggers(self, guild: discord.Guild = None) -> None:
        async with asyncio.timeout(120):
            await self.prestart_event.wait()
            if not guild:
                for guild_id, trigger_list in self.triggers.items():
                    async with self.trigger_saves[guild_id]:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            continue
                        trigger_data = {}
                        for trigger_item in trigger_list:
                            trigger_data[trigger_item.name] = await trigger_item.to_json()
                            await asyncio.sleep(0)
                        await self.config.guild(guild).trigger_list.set(trigger_data)

            else:
                async with self.trigger_saves[guild.id]:
                    trigger_data = {}
                    for trigger_item in self.triggers[guild.id]:
                        trigger_data[trigger_item.name] = await trigger_item.to_json()
                        await asyncio.sleep(0)
                    await self.config.guild(guild).trigger_list.set(trigger_data)

    async def load_trigger_cache(self, target_guild: discord.Guild = None) -> None:
        await self.bot.wait_until_red_ready()

        self.trigger_timeout = await self.config.trigger_timeout()
        if not target_guild:
            count = 0
            with capturetime("Global trigger load"):
                all_guilds = await self.config.all_guilds()
                for gid, data in all_guilds.items():
                    if "trigger_list" not in data and not data["trigger_list"]:
                        continue
                    async with self.trigger_saves[gid]:
                        new_triggers = []
                        for trigger_json in data["trigger_list"].values():
                            loaded_trigger = await Trigger.from_json(trigger_json)
                            new_triggers.append(loaded_trigger)
                        self.triggers[gid] = new_triggers
                self.prestart_event.set()
                self.startup_event.set()
                for guild_triggers in self.triggers.values():
                    count += len(guild_triggers)
            log.success("Loaded a total of {} tiggers", count)

        else:
            guild: discord.Guild = self.bot.get_guild(target_guild.id)
            async with self.trigger_saves[guild.id]:
                await self.prestart_event.wait()
                trigger_set = await self.config.guild(guild.id).trigger_list()
                new_triggers = []
                for trigger_json in trigger_set.values():
                    loaded_trigger = await Trigger.from_json(trigger_json)
                    new_triggers.append(loaded_trigger)
                    log.info("Loaded cache for {} trigger name: {} regex: {}", guild, loaded_trigger.name, loaded_trigger.regex_raw)
                self.triggers[guild.id] = new_triggers

    @commands.group(aliases=["trigger"])
    @commands.guild_only()
    async def retrigger(self, ctx: commands.Context) -> None:
        """Setup automatic triggers based on regular expressions."""

    @retrigger.command()
    @checks.has_permissions(manage_messages=True)
    async def text(
        self,
        ctx: commands.Context,
        name: TriggerExists,
        regex: ValidRegex,
        delete_after: Optional[TimedeltaConverter] = None,
        *,
        text: str,
    ) -> None:
        """Add a text response trigger.

        `<name>` name of the trigger. `<regex>` the regex that will
        determine when to respond. `[delete_after]` Optionally have the
        text autodelete must include units e.g. 2m. `<text>` response of
        the trigger.

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(embed=make_e(msg, 2))
        ctx.guild
        author = ctx.message.author.id
        if delete_after:
            if delete_after.total_seconds() > 0:
                delete_after_seconds = delete_after.total_seconds()
            if delete_after.total_seconds() < 1:
                return await ctx.send("`delete_after` must be greater than 1 second.")
        else:
            delete_after_seconds = None
        new_trigger = Trigger(name, regex, ["text"], author, text=text, created_at=ctx.message.id, delete_after=delete_after_seconds)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        await self.save_all_triggers(ctx.guild)
        await ctx.send(
            embed=make_e(f"Trigger `{name}` created.", tip=f"{len(self.triggers[ctx.guild.id])} trigger(s) have been created so far!", status="info"),
        )

    @retrigger.group(name="blocklist", aliases=["blacklist"], hidden=True)
    @checks.has_permissions(manage_messages=True)
    async def blacklist(self, ctx: commands.Context) -> None:
        """Set blocklist options for retrigger.

        blocklisting supports channels, users, or roles

        """

    @retrigger.group(name="allowlist", aliases=["whitelist"], hidden=True)
    @checks.has_permissions(manage_messages=True)
    async def whitelist(self, ctx: commands.Context) -> None:
        """Set allowlist options for retrigger.

        allowlisting supports channels, users, or roles

        """

    @retrigger.group(name="edit")
    @checks.has_permissions(manage_channels=True)
    async def _edit(self, ctx: commands.Context) -> None:
        """Edit various settings in a set trigger.

        Note: Only the server owner, Bot owner, or original
        author can edit a saved trigger. Multi triggers
        cannot be edited.

        """

    @retrigger.command(name="last", aliases=["history", "ran", "find"])
    @checks.has_permissions(manage_messages=True)
    async def last(self, ctx: commands.Context, *, content: Optional[str] = None) -> None:
        """Find the last executed trigger executed in the channel."""
        trigger = None
        found = []
        if self.find_locks[ctx.guild.id].locked():
            return await ctx.send(
                embed=make_e(
                    "I'm already searching for a trigger, please wait!",
                    tip="Triggers may still be the inital loading phase. Wait up to 5 minutes for the find to run the first time",
                    status=2,
                ),
            )
        async with self.find_locks[ctx.guild.id]:
            async with ctx.typing():
                async with asyncio.timeout(120):
                    await self.startup_event.wait()
                    if not content:
                        try:
                            record = await TriggerActionRecord.find(ctx.channel)
                            for t in self.triggers[ctx.guild.id]:
                                if record.name == t.name:
                                    found.append(t)
                                    break
                            trigger = t
                            if not trigger:
                                raise ValueError

                        except ValueError:
                            return await ctx.send(embed=make_e("No trigger for this channel found", 3))

                    else:
                        for t in self.triggers[ctx.guild.id]:
                            search = t.regex.findall(content)
                            if search:
                                found.append(t)
                        if not found:
                            if not (name_matches := [t for t in self.triggers[ctx.guild.id] if t.name.lower() == content]):
                                return await ctx.send(embed=make_e(f"No trigger matching the value `{content}` was found", 3))
                            if len(name_matches) > 1:
                                trigger_list = "\n".join(f"`Name: {t.name}`\n`Regex: {t.regex_raw}`\n" for t in name_matches)
                                return await ctx.send(
                                    embed=make_e(
                                        f"No trigger matching the value `{content}` was found **but** I did find the following triggers with that name\n {trigger_list}",
                                        2,
                                    ),
                                )
                            else:
                                found.append(name_matches.pop())
                        if len(found) > 1:
                            trigger_list = "\n".join(f"`Name: {t.name}`\n`Regex: {t.regex_raw}`\n" for t in found)
                            return await ctx.send(
                                embed=make_e(
                                    f"I found mulitple triggers matching this query.\nUse `;trigger info <name>` to view details\n\n{trigger_list}",
                                    2,
                                ),
                            )

                    await ctx.invoke(ctx.bot.get_command("retrigger list"), guild_id=ctx.guild.id, trigger=found[0])

    @retrigger.command()
    @checks.has_permissions(manage_messages=True)
    async def cooldown(self, ctx: commands.Context, trigger: TriggerExists, time: int, style: str = "guild") -> None:
        """Set cooldown options for retrigger.

        `<trigger>` is the name of the trigger. `<time>` is a time in
        seconds until the trigger will run again set a time of 0 or less
        to remove the cooldown `[style=guild]` must be either `guild`,
        `server`, `channel`, `user`, or `member`

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if style not in ["guild", "server", "channel", "user", "member"]:
            msg = "Style must be either `guild`, `server`, `channel`, `user`, or `member`."
            await ctx.send(msg)
            return
        msg = "Cooldown of {time}s per {style} set for Trigger `{name}`."
        if style in {"user", "member"}:
            style = "author"
        cooldown = {"time": time, "style": style, "last": 0} if style in {"guild", "server"} else {"time": time, "style": style, "last": []}
        if time <= 0:
            cooldown = {}
            msg = "Cooldown for Trigger `{name}` reset."
        trigger_list = await self.config.guild(ctx.guild).trigger_list()
        trigger.cooldown = cooldown
        trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        await self.config.guild(ctx.guild).trigger_list.set(trigger_list)
        await ctx.send(msg.format(time=time, style=style, name=trigger.name))

    @whitelist.command(name="add")
    @checks.has_permissions(manage_messages=True)
    async def whitelist_add(self, ctx: commands.Context, trigger: TriggerExists, *channel_user_role: ChannelUserRole) -> None:
        """Add a channel, user, or role to triggers allowlist.

        `<trigger>` is the name of the trigger. `[channel_user_role...]`
        is the channel, user or role to allowlist (You can supply more
        than one of any at a time)

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be allowed")
        for obj in channel_user_role:
            if obj.id not in trigger.whitelist:
                async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
                    trigger.whitelist.append(obj.id)
                    trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} added `{list_type}` to its allowlist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type, name=trigger.name))

    @whitelist.command(name="remove", aliases=["rem", "del"])
    @checks.has_permissions(manage_messages=True)
    async def whitelist_remove(self, ctx: commands.Context, trigger: TriggerExists, *channel_user_role: ChannelUserRole) -> None:
        """Remove a channel, user, or role from triggers allowlist.

        `<trigger>` is the name of the trigger. `[channel_user_role...]`
        is the channel, user or role to remove from the allowlist (You
        can supply more than one of any at a time)

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be removed from the allowlist.")
        for obj in channel_user_role:
            if obj.id in trigger.whitelist:
                async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
                    trigger.whitelist.remove(obj.id)
                    trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} removed `{list_type}` from its allowlist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type, name=trigger.name))

    @blacklist.command(name="add")
    @checks.has_permissions(manage_messages=True)
    async def blacklist_add(self, ctx: commands.Context, trigger: TriggerExists, *channel_user_role: ChannelUserRole) -> None:
        """Add a channel, user, or role to triggers blocklist.

        `<trigger>` is the name of the trigger. `[channel_user_role...]`
        is the channel, user or role to blocklist (You can supply more
        than one of any at a time)

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be blocked.")
        for obj in channel_user_role:
            if obj.id not in trigger.blacklist:
                async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
                    trigger.blacklist.append(obj.id)
                    trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} added `{list_type}` to its blocklist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type, name=trigger.name))

    @blacklist.command(name="remove", aliases=["rem", "del"])
    @checks.has_permissions(manage_messages=True)
    async def blacklist_remove(self, ctx: commands.Context, trigger: TriggerExists, *channel_user_role: ChannelUserRole) -> None:
        """Remove a channel, user, or role from triggers blocklist.

        `<trigger>` is the name of the trigger. `[channel_user_role...]`
        is the channel, user or role to remove from the blocklist (You
        can supply more than one of any at a time)

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be removed from the blocklist.")
        for obj in channel_user_role:
            if obj.id in trigger.blacklist:
                async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
                    trigger.blacklist.remove(obj.id)
                    trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} removed `{list_type}` from its blocklist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type, name=trigger.name))

    @_edit.command(name="regex")
    @checks.has_permissions(manage_messages=True)
    async def edit_regex(self, ctx: commands.Context, trigger: TriggerExists, *, regex: ValidRegex) -> None:
        """Edit the regex of a saved trigger.

        `<trigger>` is the name of the trigger. `<regex>` The new regex
        pattern to use.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.regex = re.compile(regex)
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} regex changed to ```bf\n{regex}\n```"
        await ctx.send(msg.format(name=trigger.name, regex=regex))

    @_edit.command(name="ocr")
    @commands.check(lambda ctx: TriggerHandler.ALLOW_OCR)
    @checks.has_permissions(manage_messages=True)
    async def toggle_ocr_search(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Toggle whether to use Optical Character Recognition to search for text
        within images.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.ocr_search = not trigger.ocr_search
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} OCR Search set to: {ocr_search}"
        await ctx.send(msg.format(name=trigger.name, ocr_search=trigger.ocr_search))

    @_edit.command(name="readfilenames", aliases=["filenames"])
    @checks.has_permissions(manage_messages=True)
    async def toggle_filename_search(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Toggle whether to search message attachment filenames.

        Note: This will append all attachments in a message to the message content. This **will not**
        download and read file content using regex.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.read_filenames = not trigger.read_filenames
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} read filenames set to: {read_filenames}"
        await ctx.send(msg.format(name=trigger.name, read_filenames=trigger.read_filenames))

    @_edit.command(name="reply", aliases=["replies"])
    @checks.has_permissions(manage_messages=True)
    async def set_reply(self, ctx: commands.Context, trigger: TriggerExists, set_to: Optional[bool] = None) -> None:
        """Set whether or not to reply to the triggered message.

        `<trigger>` is the name of the trigger. `[set_to]` `True` will
        reply with a notificaiton, `False` will reply without a
        notification, leaving this blank will clear replies entirely.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.reply = set_to
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} replies set to: {set_to}"
        await ctx.send(msg.format(name=trigger.name, set_to=trigger.reply))

    @_edit.command(name="usermention", aliases=["userping"])
    @checks.has_permissions(manage_messages=True)
    async def set_user_mention(self, ctx: commands.Context, trigger: TriggerExists, set_to: bool) -> None:
        """Set whether or not to send this trigger will mention users in the
        reply.

        `<trigger>` is the name of the trigger. `[set_to]` either `true`
        or `false` on whether to allow this trigger to actually ping the
        users in the message.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.user_mention = set_to
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} user mentions set to: {set_to}"
        await ctx.send(msg.format(name=trigger.name, set_to=trigger.user_mention))

    @_edit.command(name="everyonemention", aliases=["everyoneping"])
    @checks.has_permissions(manage_messages=True, mention_everyone=True)
    async def set_everyone_mention(self, ctx: commands.Context, trigger: TriggerExists, set_to: bool) -> None:
        """Set whether or not to send this trigger will allow everyone mentions.

        `<trigger>` is the name of the trigger. `[set_to]` either `true`
        or `false` on whether to allow this trigger to actually ping
        everyone if the bot has correct permissions.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.everyone_mention = set_to
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} everyone mentions set to: {set_to}"
        await ctx.send(msg.format(name=trigger.name, set_to=trigger.everyone_mention))

    @_edit.command(name="rolemention", aliases=["roleping"])
    @checks.has_permissions(manage_messages=True, mention_everyone=True)
    async def set_role_mention(self, ctx: commands.Context, trigger: TriggerExists, set_to: bool) -> None:
        """Set whether or not to send this trigger will allow role mentions.

        `<trigger>` is the name of the trigger. `[set_to]` either `true`
        or `false` on whether to allow this trigger to actually ping
        roles if the bot has correct permissions.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.role_mention = set_to
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} role mentions set to: {set_to}"
        await ctx.send(msg.format(name=trigger.name, set_to=trigger.role_mention))

    @_edit.command(name="edited")
    @checks.has_permissions(manage_messages=True)
    async def toggle_check_edits(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Toggle whether the bot will listen to edited messages as well as
        on_message for the specified trigger.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.check_edits = not trigger.check_edits
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} check edits set to: {ignore_edits}"
        await ctx.send(msg.format(name=trigger.name, ignore_edits=trigger.check_edits))

    @_edit.command(name="text", aliases=["msg"])
    @checks.has_permissions(manage_messages=True)
    async def edit_text(self, ctx: commands.Context, trigger: TriggerExists, *, text: str) -> None:
        """Edit the text of a saved trigger.

        `<trigger>` is the name of the trigger. `<text>` The new text to
        respond with.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if trigger.multi_payload:
            return await ctx.send("You cannot edit multi triggers response.")
        if "text" not in trigger.response_type:
            return await ctx.send("That trigger cannot be edited this way.")
        trigger.text = text
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} text changed to `{text}`"
        await ctx.send(msg.format(name=trigger.name, text=text))

    @_edit.command(name="chance", aliases=["chances"])
    @checks.has_permissions(manage_messages=True)
    async def edit_chance(self, ctx: commands.Context, trigger: TriggerExists, chance: int) -> None:
        """Edit the chance a trigger will execute.

        `<trigger>` is the name of the trigger. `<chance>` The chance
        the trigger will execute in form of 1 in chance.

        Set the `chance` to 0 to remove the chance and always perform
        the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if chance < 0:
            return await ctx.send("You cannot have a negative chance of triggers happening.")
        trigger.chance = chance
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} chance changed to `1 in {chance}`" if chance else "Trigger {name} chance changed to always."
        await ctx.send(msg.format(name=trigger.name, chance=str(chance)))

    @_edit.command(name="deleteafter", aliases=["autodelete", "delete"])
    @checks.has_permissions(manage_messages=True)
    async def edit_delete_after(self, ctx: commands.Context, trigger: TriggerExists, *, delete_after: TimedeltaConverter = None) -> None:
        """Edit the delete_after parameter of a saved text trigger.

        `<trigger>` is the name of the trigger.
        `<delete_after>` The time until the message is deleted must include units.
        Example: `;retrigger edit deleteafter trigger 2 minutes`

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if "text" not in trigger.response_type:
            return await ctx.send("That trigger cannot be edited this way.")
        if delete_after:
            if delete_after.total_seconds() > 0:
                delete_after_seconds = delete_after.total_seconds()
            if delete_after.total_seconds() < 1:
                return await ctx.send("`delete_after` must be greater than 1 second.")
        else:
            delete_after_seconds = None
        trigger.delete_after = delete_after_seconds
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} will now delete after `{time}` seconds."
        await ctx.send(msg.format(name=trigger.name, time=delete_after_seconds))

    @_edit.command(name="ignorecommands")
    @checks.has_permissions(manage_messages=True)
    async def edit_ignore_commands(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Toggle the trigger ignoring command messages entirely.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        trigger.ignore_commands = not trigger.ignore_commands
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} ignoring commands set to `{text}`"
        await ctx.send(msg.format(name=trigger.name, text=trigger.ignore_commands))

    @_edit.command(name="command", aliases=["cmd"])
    @checks.has_permissions(manage_messages=True)
    async def edit_command(self, ctx: commands.Context, trigger: TriggerExists, *, command: str) -> None:
        """Edit the text of a saved trigger.

        `<trigger>` is the name of the trigger. `<command>` The new
        command for the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if trigger.multi_payload:
            return await ctx.send("You cannot edit multi triggers response.")
        cmd_list = command.split(" ")
        existing_cmd = self.bot.get_command(cmd_list[0])
        if existing_cmd is None:
            await ctx.send(f"`{command}` doesn't seem to be an available command.")
            return
        if "command" not in trigger.response_type:
            return await ctx.send("That trigger cannot be edited this way.")
        trigger.text = command
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} command changed to `{command}`"
        await ctx.send(msg.format(name=trigger.name, command=command))

    @_edit.command(name="role", aliases=["roles"])
    @checks.has_permissions(manage_roles=True)
    async def edit_roles(self, ctx: commands.Context, trigger: TriggerExists, *roles: discord.Role) -> None:
        """Edit the added or removed roles of a saved trigger.

        `<trigger>` is the name of the trigger. `<roles>` space
        separated list of roles or ID's to edit on the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if trigger.multi_payload:
            return await ctx.send("You cannot edit multi triggers response.")
        for role in roles:
            if role >= ctx.me.top_role:
                return await ctx.send("I can't assign roles higher than my own.")
            if ctx.author.id == ctx.guild.owner_id:
                continue
            if role >= ctx.author.top_role:
                return await ctx.send("I can't assign roles higher than you are able to assign.")
        role_ids = [r.id for r in roles]
        if not any(t for t in trigger.response_type if t in ["add_role", "remove_role"]):
            return await ctx.send("That trigger cannot be edited this way.")
        trigger.text = role_ids
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} role edits changed to `{roles}`"
        await ctx.send(msg.format(name=trigger.name, roles=humanize_list([r.name for r in roles])))

    @_edit.command(name="react", aliases=["emojis"])
    @checks.has_permissions(manage_messages=True)
    async def edit_reactions(self, ctx: commands.Context, trigger: TriggerExists, *emojis: ValidEmoji) -> None:
        """Edit the emoji reactions of a saved trigger.

        `<trigger>` is the name of the trigger. `<emojis>` The new
        emojis to be used in the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        if not await self.can_edit(ctx.author, trigger):
            return await ctx.send("You are not authorized to edit this trigger.")
        if "react" not in trigger.response_type:
            return await ctx.send("That trigger cannot be edited this way.")
        trigger.text = emojis
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        self.triggers[ctx.guild.id].append(trigger)
        msg = "Trigger {name} reactions changed to {emojis}"
        emoji_s = [f"<{e}>" for e in emojis if len(e) > 5] + [e for e in emojis if len(e) < 5]
        await ctx.send(msg.format(name=trigger.name, emojis=humanize_list(emoji_s)))

    @retrigger.command(name="enable")
    @checks.has_permissions(manage_messages=True)
    async def enable_trigger(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Enable a trigger that has been disabled either by command or
        automatically.

        `<trigger>` is the name of the trigger.

        """
        async with asyncio.timeout(30), ctx.typing():
            if isinstance(trigger, str):
                return await ctx.send(embed=make_e(f"Trigger `{trigger}` doesn't exist.", 2))
            trigger.enabled = True
            guild: discord.Guild = ctx.guild
            async with self.trigger_saves[guild.id]:
                for channel in guild.channels:
                    if isinstance(channel, discord.TextChannel):
                        _key = f'disabled_trigger{xxh32_hexdigest(f"{trigger.name}{trigger.regex}{channel.id}")}'
                        await self.bot.redis.delete(_key)
            async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
                trigger_list[trigger.name] = await trigger.to_json()
            await self.remove_trigger_from_cache(ctx.guild.id, trigger)
            self.triggers[ctx.guild.id].append(trigger)
            await ctx.send(embed=make_e(f"Trigger {trigger.name} has been enabled."))

    @retrigger.command(name="disable")
    @checks.has_permissions(manage_messages=True)
    async def disable_trigger(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Disable a trigger.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, str):
            return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
        trigger.enabled = False
        async with self.config.guild(ctx.guild).trigger_list() as trigger_list:
            trigger_list[trigger.name] = await trigger.to_json()
        await self.remove_trigger_from_cache(ctx.guild.id, trigger)
        msg = "Trigger {name} has been disabled."
        await ctx.send(msg.format(name=trigger.name))

    @retrigger.command(hidden=True)
    @checks.is_owner()
    async def timeout(self, ctx: commands.Context, timeout: int) -> None:
        """Set the timeout period for searching triggers.

        `<timeout>` is number of seconds until regex searching is kicked
        out.

        """
        if timeout > 1:
            msg = await ctx.send(
                "Increasing this could cause the bot to become unstable or allow bad regex patterns to continue to exist causing slow downs and even fatal crashes on the bot. Do you wish to continue?",
            )
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, user=ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=30)
            except TimeoutError:
                return await ctx.send("Not changing regex timeout time.")
            if pred.result:
                await self.config.trigger_timeout.set(timeout)
                self.trigger_timeout = timeout
                await ctx.tick()
            else:
                await ctx.send("Not changing regex timeout time.")
        elif timeout > 10:
            return await ctx.send(f"{timeout} seconds is too long, you may want to look at `{ctx.clean_prefix}retrigger bypass`")
        else:
            timeout = max(timeout, 1)
            await self.config.trigger_timeout.set(timeout)
            self.trigger_timeout = timeout
            await ctx.send(f"Regex search timeout set to {timeout}")

    @retrigger.command(hidden=True)
    @checks.is_owner()
    async def bypass(self, ctx: commands.Context, bypass: bool) -> None:
        """Bypass patterns being kicked from memory until reload.

        **Warning:** Enabling this can allow mods and admins to create triggers
        that cause catastrophic backtracking which can lead to the bot crashing
        unexpectedly. Only enable in servers where you trust the admins not to
        mess with the bot.

        """
        if bypass:
            msg = await ctx.send(
                "Bypassing this could cause the bot to become unstable or allow bad regex patterns to continue to exist causing slow downs and even fatal crashes on the bot. Do you wish to continue?",
            )
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, user=ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=30)
            except TimeoutError:
                return await ctx.send("Not bypassing safe Regex search.")
            if pred.result:
                await self.config.guild(ctx.guild).bypass.set(bypass)
                await ctx.tick()
            else:
                await ctx.send("Not bypassing safe Regex search.")
        else:
            await self.config.guild(ctx.guild).bypass.set(bypass)
            await ctx.send("Safe Regex search re-enabled.")

    @retrigger.command(usage="[trigger]")
    async def list(self, ctx: commands.Context, guild_id: Optional[int], trigger: TriggerExists = None) -> None:
        """List all the triggers configured for the server."""
        guild = ctx.guild
        if guild_id and await ctx.bot.is_owner(ctx.author):
            guild = ctx.bot.get_guild(guild_id) or ctx.guild
        index = 0
        if guild.id not in self.triggers or not self.triggers[guild.id]:
            msg = "There are no triggers setup on this server."
            await ctx.send(embed=make_e(msg, 2))
            return
        if trigger:
            if isinstance(trigger, str):
                return await ctx.send(f"Trigger `{trigger}` doesn't exist.")
            for t in self.triggers[guild.id]:
                if t.name == trigger.name:
                    index = self.triggers[guild.id].index(t)
        await ReTriggerMenu(
            source=ReTriggerPages(triggers=self.triggers[guild.id], guild=guild),
            delete_message_after=False,
            clear_reactions_after=True,
            timeout=60,
            cog=self,
            page_start=index,
        ).start(ctx=ctx)

    @retrigger.command(aliases=["reset"])
    @checks.has_permissions(administrator=True)
    async def clear(self, ctx: commands.Context) -> None:
        """Remove all triggers in the server."""
        async with ctx.typing(), asyncio.timeout(60):
            await self.startup_event.wait()
            if not self.triggers[ctx.guild.id]:
                return await ctx.send(embed=make_e("No triggers for this server found", 2))
            confirmed, _msg = await yesno("Are you sure you want to delete all triggers in this server?")
            if not confirmed:
                return
            async with self.trigger_saves[ctx.guild.id]:
                count = len(self.triggers[ctx.guild.id])
                self.triggers[ctx.guild.id] = []
            await self.save_all_triggers(ctx.guild)
        await ctx.send(embed=make_e(f"Removed {count} triggers"))

    @retrigger.command(aliases=["del", "rem", "delete"])
    @checks.has_permissions(manage_messages=True)
    async def remove(self, ctx: commands.Context, trigger: TriggerExists) -> None:
        """Remove a specified trigger.

        `<trigger>` is the name of the trigger.

        """
        if isinstance(trigger, Trigger):
            await self.remove_trigger(ctx.guild.id, trigger.name)
            return await ctx.send(embed=make_e(f"Trigger `{trigger.name}` removed."))

        await ctx.send(embed=make_e(f"Trigger `{str(trigger)}` doesn't exist.", 2))

    @retrigger.command(aliases=["randomtext", "rtext"])
    @checks.has_permissions(manage_messages=True)
    async def random(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex) -> None:
        """Add a random text response trigger.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        text = await self.wait_for_multiple_responses(ctx)
        if not text:
            await ctx.send("No responses supplied")
            return
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["randtext"], author, text=text, created_at=ctx.message.id)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    # @retrigger.command()
    # @checks.has_permissions(manage_messages=True)
    # async def dm(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *, text: str) -> None:
    #     """
    #     Add a dm response trigger

    #     `<name>` name of the trigger
    #     `<regex>` the regex that will determine when to respond
    #     `<text>` response of the trigger

    #     """
    #     if type(name) != str:
    #     if ctx.guild.id not in self.triggers:

    # @retrigger.command()
    # @checks.has_permissions(manage_messages=True)
    # async def dmme(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *, text: str) -> None:
    #     """
    #     Add trigger to DM yourself

    #     `<name>` name of the trigger
    #     `<regex>` the regex that will determine when to respond
    #     `<text>` response of the trigger

    #     """
    #     if type(name) != str:
    #     if ctx.guild.id not in self.triggers:

    @retrigger.command()
    @checks.has_permissions(manage_nicknames=True)
    async def rename(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *, text: str) -> None:
        """Add trigger to rename users.

        `<name>` name of the trigger. `<regex>` the regex that will
        determine when to respond. `<text>` new users nickanme.

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["rename"], author, text=text, created_at=ctx.message.id)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    @retrigger.command()
    @checks.has_permissions(manage_messages=True)
    async def image(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, image_url: str = None) -> None:
        """Add an image/file response trigger.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond `image_url` optional image_url if none
        is provided the bot will ask to upload an image

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        if ctx.message.attachments != []:
            attachment_url = ctx.message.attachments[0].url
            filename = await self.save_image_location(attachment_url, guild)
        elif image_url is not None:
            filename = await self.save_image_location(image_url, guild)
        else:
            msg = await self.wait_for_image(ctx)
            if not msg or not msg.attachments:
                return
            image_url = msg.attachments[0].url
            filename = await self.save_image_location(image_url, guild)
        if not filename:
            return await ctx.send("That is not a valid file link.")
        new_trigger = Trigger(name, regex, ["image"], author, image=filename, created_at=ctx.message.id)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    # @retrigger.command(aliases=["randimage", "randimg", "rimage", "rimg"])
    # @checks.has_permissions(manage_messages=True)
    #
    # async def randomimage(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex) -> None:
    #     """
    #     Add a random image/file response trigger.

    #     `<name>` name of the trigger `<regex>` the regex that will
    #     determine when to respond

    #     """
    #     if not isinstance(name, str):

    #     if ctx.guild.id not in self.triggers:

    # @retrigger.command()
    # @checks.has_permissions(manage_messages=True)
    #
    # async def imagetext(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, text: str, image_url: str = None) -> None:
    #     """
    #     Add an image/file response with text trigger.

    #     `<name>` name of the trigger `<regex>` the regex that will
    #     determine when to respond `<text>` the triggered text response
    #     `[image_url]` optional image_url if none is provided the bot
    #     will ask to upload an image

    #     """
    #     if not isinstance(name, str):
    #     if ctx.message.attachments != []:
    #     if image_url is None:
    #         if not msg or not msg.attachments:
    #     if not filename:
    #     if ctx.guild.id not in self.triggers:

    @retrigger.command(hidden=True)
    @checks.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, name: TriggerExists, regex: str) -> None:
        """Add a trigger to ban users for saying specific things found with regex
        This respects hierarchy so ensure the bot role is lower in the list
        than mods and admin so they don't get banned by accident.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["ban"], author, created_at=ctx.message.id, check_edits=True)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    @retrigger.command()
    @checks.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, name: TriggerExists, regex: str) -> None:
        """Add a trigger to kick users for saying specific things found with regex
        This respects hierarchy so ensure the bot role is lower in the list
        than mods and admin so they don't get kicked by accident.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["kick"], author, created_at=ctx.message.id, check_edits=True)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    @retrigger.command(aliases=["cmd"])
    @checks.has_permissions(manage_messages=True)
    async def command(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *, command: str) -> None:
        """Add a command trigger.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond `<command>` the command that will be
        triggered, do not add ; prefix

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        cmd_list = command.split(" ")
        existing_cmd = self.bot.get_command(cmd_list[0])
        if existing_cmd is None:
            await ctx.send(f"{command} doesn't seem to be an available command.")
            return
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["command"], author, text=command, created_at=ctx.message.id)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    # @retrigger.command(aliases=["cmdmock"], hidden=True)
    # @checks.has_permissions(administrator=True)
    # async def mock(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *, command: str) -> None:
    #     """
    #     Add a trigger for command as if you used the command.

    #     `<name>` name of the trigger
    #     `<regex>` the regex that will determine when to respond
    #     `<command>` the command that will be triggered, do not add ; prefix
    #     **Warning:** This function can let other users run a command on your behalf,
    #     use with caution.

    #     """
    #     if not pred.result:
    #     if not isinstance(name, str):
    #     if existing_cmd is None:
    #     if ctx.guild.id not in self.triggers:

    @retrigger.command(aliases=["deletemsg"])
    @checks.has_permissions(manage_messages=True)
    async def filter(self, ctx: commands.Context, name: TriggerExists, check_filenames: Optional[bool] = False, *, regex: str) -> None:
        """Add a trigger to delete a message.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        new_trigger = Trigger(name, regex, ["delete"], author, read_filenames=check_filenames, created_at=ctx.message.id, check_edits=True)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")

    # @retrigger.command()
    # @checks.has_permissions(manage_roles=True)
    #
    # async def addrole(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *roles: discord.Role) -> None:
    #     """
    #     Add a trigger to add a role.

    #     `<name>` name of the trigger `<regex>` the regex that will
    #     determine when to respond `[role...]` the roles applied when the
    #     regex pattern matches space separated

    #     """
    #     if not isinstance(name, str):
    #     for role in roles:
    #         if role >= ctx.me.top_role:
    #         if ctx.author.id == ctx.guild.owner_id:
    #         if role >= ctx.author.top_role:
    #     if ctx.guild.id not in self.triggers:

    # @retrigger.command()
    # @checks.has_permissions(manage_roles=True)
    #
    # async def removerole(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *roles: discord.Role) -> None:
    #     """
    #     Add a trigger to remove a role.

    #     `<name>` name of the trigger `<regex>` the regex that will
    #     determine when to respond `[role...]` the roles applied when the
    #     regex pattern matches space separated

    #     """
    #     if not isinstance(name, str):
    #     for role in roles:
    #         if role >= ctx.me.top_role:
    #         if ctx.author.id == ctx.guild.owner_id:
    #         if role >= ctx.author.top_role:
    #     if ctx.guild.id not in self.triggers:

    @retrigger.command()
    @checks.has_permissions(administrator=True)
    async def multi(self, ctx: commands.Context, name: TriggerExists, regex: ValidRegex, *multi_response: MultiResponse) -> None:
        """Add a multiple response trigger.

        `<name>` name of the trigger `<regex>` the regex that will
        determine when to respond `[multi_response...]` the list of
        actions the bot will perform

        Multiple responses start with the name of the action which must
        be one of the listed options below, followed by a `;` if there
        is a followup response add a space for the next trigger
        response. If you want to add or remove multiple roles those may
        be followed up with additional `;` separations. e.g. `;retrigger
        multi test \\btest\\b \"dm;You said a bad word!\" filter
        "remove_role;Regular Member" add_role;Timeout` Will attempt to
        DM the user, delete their message, remove their `@Regular
        Member` role and add the `@Timeout` role simultaneously.

        Available options: dm dmme remove_role add_role ban kick text
        filter or delete react rename command

        See
        https://regex101.com/
        for help building a regex pattern.

        """
        if not isinstance(name, str):
            msg = f"{name.name} is already a trigger name"
            return await ctx.send(msg)
        guild = ctx.guild
        author = ctx.message.author.id
        if not [i[0] for i in multi_response]:
            return await ctx.send("You have no actions provided for this trigger.")
        new_trigger = Trigger(name, regex, [i[0] for i in multi_response], author, multi_payload=multi_response, created_at=ctx.message.id)
        if ctx.guild.id not in self.triggers:
            self.triggers[ctx.guild.id] = []
        self.triggers[ctx.guild.id].append(new_trigger)
        trigger_list = await self.config.guild(guild).trigger_list()
        trigger_list[name] = await new_trigger.to_json()
        await self.config.guild(guild).trigger_list.set(trigger_list)
        await ctx.send(f"Trigger `{name}` set.")
