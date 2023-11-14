"""RemindMe cog for Melanie-DiscordBot ported and enhanced by PhasecoreX."""
from __future__ import annotations

import asyncio
import time
from abc import ABC
from datetime import MAXYEAR, datetime, timezone
from typing import Union

import discord
import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from loguru import logger as log
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import humanize_list
from pyparsing import ParseException

from executionstracker.exe import ExecutionsTracker
from melanie import create_task
from remindme.c_reminder import ReminderCommands
from remindme.c_remindmeset import RemindMeSetCommands
from remindme.pcx_lib import reply
from remindme.reminder_parse import ReminderParser

SEEN_QUERY = """select distinct channel_id, created_at
from guild_messages
where user_id = $1
  and created_at > now() - interval '10 day'
order by created_at desc
limit 10;"""


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """This allows the metaclass used for proper type detection to coexist with
    discord.py's metaclass.
    """


class RemindMe(ReminderCommands, RemindMeSetCommands, commands.Cog, metaclass=CompositeMetaClass):
    """Never forget anything anymore."""

    __version__ = "3.0.2"

    default_global_settings = {"schema_version": 0, "total_sent": 0, "max_user_reminders": 20}
    default_guild_settings = {"me_too": False}
    default_reminder_settings = {
        "text": "",
        "created": None,
        "expires": None,
        "jump_link": None,
        "repeat": {},
    }  # str  # seconds from epoch int  # seconds from epoch int  # str  # relativedelta dict
    SEND_DELAY_SECONDS = 30

    def __init__(self, bot: Melanie) -> None:
        """Set up the cog."""
        super().__init__()
        self.bot: Melanie = bot
        self.cal = pdt.Calendar()

        self.config = Config.get_conf(self, identifier=1224364860, force_registration=True)
        self.config.register_global(**self.default_global_settings)
        self.config.register_guild(**self.default_guild_settings)
        # user id -> user reminder id
        self.config.init_custom("REMINDER", 2)
        self.config.register_custom("REMINDER", **self.default_reminder_settings)
        self.bg_loop_task = None
        self.next_reminder_to_send = {}
        self.search_for_next_reminder = True
        self.me_too_reminders = {}
        self.clicked_me_too_reminder = {}
        self.reminder_emoji = "\N{BELL}"
        self.reminder_parser = ReminderParser()
        self.problematic_reminders = []
        self.sent_retry_warning = False

    #
    # Melanie methods
    #

    def cog_unload(self) -> None:
        """Clean up when cog shuts down."""
        if self.bg_loop_task:
            self.bg_loop_task.cancel()

    # Initialization methods
    #

    async def initialize(self) -> None:
        """Perform setup actions before loading cog."""
        await self._migrate_config()
        # async with self.config.custom("REMINDER").all() as current_reminders:
        #     for _data in current_reminders.values():
        #         for data in _data.values():
        #             # if str(data["text"]).lower().startswith("to"):

        self._enable_bg_loop()

    async def _migrate_config(self) -> None:
        """Perform some configuration migrations."""
        schema_version = await self.config.schema_version()

        schema_1_migration_reminders = []
        if schema_version < 1:
            # Add/generate USER_REMINDER_ID, rename some fields
            current_reminders = await self.config.get_raw("reminders", default=[])
            new_reminders = []
            user_reminder_ids = {}
            for reminder in current_reminders:
                user_reminder_id = user_reminder_ids.get(reminder["ID"], 1)
                new_reminder = {
                    "USER_REMINDER_ID": user_reminder_id,
                    "USER_ID": reminder["ID"],
                    "REMINDER": " ".join(reminder["TEXT"].split()).strip(),
                    "FUTURE": reminder["FUTURE"],
                    "FUTURE_TEXT": reminder["FUTURE_TEXT"],
                    "JUMP_LINK": None,
                }
                user_reminder_ids[reminder["ID"]] = user_reminder_id + 1
                new_reminders.append(new_reminder)
            schema_1_migration_reminders = new_reminders
            await self.config.schema_version.set(1)

        if schema_version < 2:
            # Migrate to REMINDER custom config group
            current_reminders = schema_1_migration_reminders or await self.config.get_raw("reminders", default=[])
            for reminder in current_reminders:
                # Get normalized expires datetime
                try:
                    expires_normalized = datetime.fromtimestamp(reminder["FUTURE"], timezone.utc)
                except (OverflowError, ValueError):
                    expires_normalized = datetime(MAXYEAR, 12, 31, 23, 59, 59, 0, tzinfo=timezone.utc)
                # Try and convert the future text over to an actual point in time
                created_converted = expires_normalized - relativedelta(seconds=1)
                log.debug("Converting to relativedelta object: {}", reminder["FUTURE_TEXT"])
                try:
                    parse_result = self.reminder_parser.parse(reminder["FUTURE_TEXT"].strip())
                    in_dict = parse_result["in"]
                    if not in_dict:
                        raise ParseException
                    in_delta = relativedelta(**in_dict)
                    created_converted = expires_normalized - in_delta
                    log.debug("Successfully converted to relativedelta object: {}", self.humanize_relativedelta(in_delta))
                except ParseException:
                    log.warning('Failed to convert to datetime object for migration: {}, using "1 second" ago as created time', reminder["FUTURE_TEXT"])
                # Required fields
                new_reminder = {
                    "text": " ".join(reminder["REMINDER"].split()),
                    "created": int(created_converted.timestamp()),
                    "expires": int(expires_normalized.timestamp()),
                    "jump_link": reminder["JUMP_LINK"],
                }
                # Optional fields
                if "REPEAT" in reminder and reminder["REPEAT"]:
                    new_reminder["repeat"] = self.relativedelta_to_dict(relativedelta(seconds=reminder["REPEAT"]))
                # Save config

                await self.config.custom("REMINDER", str(reminder["USER_ID"]), str(reminder["USER_REMINDER_ID"])).set(new_reminder)
            await self.config.clear_raw("reminders")
            await self.config.schema_version.set(2)

    #
    # Listener methods
    #

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.raw_models.RawReactionActionEvent) -> None:
        """Watches for bell reactions on reminder messages."""
        if str(payload.emoji) != self.reminder_emoji:
            return
        if not payload.guild_id or await self.bot.cog_disabled_in_guild_raw(self.qualified_name, payload.guild_id):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        if not await self.config.guild(guild).me_too():
            return
        member = guild.get_member(payload.user_id)
        if member.bot:
            return

        try:
            reminder = self.me_too_reminders[payload.message_id]
            clicked_set = self.clicked_me_too_reminder[payload.message_id]
            if member.id in clicked_set:
                return  # User clicked the bell again, not going to add a duplicate reminder
            clicked_set.add(member.id)
            if await self.insert_reminder(member.id, reminder):
                expires_delta = relativedelta(
                    datetime.fromtimestamp(reminder["expires"], timezone.utc),
                    datetime.fromtimestamp(reminder["created"], timezone.utc),
                )
                repeat_delta = None
                if "repeat" in reminder and reminder["repeat"]:
                    repeat_delta = relativedelta(reminder["repeat"])
                message = "Hello! I will also send you "
                if repeat_delta:
                    message += f"those repeating reminders every {self.humanize_relativedelta(repeat_delta)}"
                else:
                    message += f"that reminder in {self.humanize_relativedelta(expires_delta)} (<t:{reminder['expires']}:f>)"
                if repeat_delta and expires_delta != repeat_delta:
                    message += f", with the first reminder in {self.humanize_relativedelta(expires_delta)} (<t:{reminder['expires']}:f>)."
                else:
                    message += "."
                await member.send(message)
            else:
                await self.send_too_many_message(member)
        except KeyError:
            return

    #
    # Background loop methods
    #

    def _enable_bg_loop(self) -> None:
        """Set up the background loop task."""
        self.bg_loop_task = create_task(self._bg_loop())

    async def _bg_loop(self) -> None:
        """Background loop."""
        await self.bot.waits_uptime_for(60)
        await self.bot.wait_until_ready()
        self.search_for_next_reminder = True
        while True:
            current_time_seconds = int(datetime.now(timezone.utc).timestamp())
            # Check if we need to send the current reminder
            if not self.next_reminder_to_send or current_time_seconds < self.next_reminder_to_send["expires"]:
                await asyncio.sleep(1)
            else:
                await self._send_reminder(self.next_reminder_to_send)
                self.search_for_next_reminder = True
            # Check if we need to retry a failed reminder
            if self.problematic_reminders and not current_time_seconds % 15:
                retry_reminder = self.problematic_reminders.pop(0)
                log.debug("Retrying user={}, id={}...", retry_reminder["user_id"], retry_reminder["user_reminder_id"])
                await self._send_reminder(retry_reminder)
            # Find a new reminder
            if self.search_for_next_reminder:
                log.debug("Looking for next reminder...")
                self.next_reminder_to_send = {}
                self.search_for_next_reminder = False
                all_reminders = await self.config.custom("REMINDER").all()  # Does NOT return default values
                existing_problematic_reminders = []
                for user_id, users_reminders in all_reminders.items():
                    for user_reminder_id, partial_reminder in users_reminders.items():
                        # If the reminder is expiring sooner than the one we have on deck to send...
                        if not self.next_reminder_to_send or partial_reminder["expires"] < self.next_reminder_to_send["expires"]:
                            full_reminder = self._get_full_reminder_from_partial(int(user_id), int(user_reminder_id), partial_reminder)
                            if full_reminder not in self.problematic_reminders:
                                self.next_reminder_to_send = full_reminder.copy()
                            else:
                                existing_problematic_reminders.append(full_reminder.copy())

                # Update retry list
                self.problematic_reminders = existing_problematic_reminders
                # Notify owners that there is a reminder that failed to send and is now retrying
                if self.problematic_reminders and not self.sent_retry_warning:
                    self.sent_retry_warning = True

                elif self.sent_retry_warning and not self.problematic_reminders:
                    self.sent_retry_warning = False

                if self.next_reminder_to_send:
                    log.debug(
                        "Next reminder is for user={}, id={}. It will be sent in {}.",
                        self.next_reminder_to_send["user_id"],
                        self.next_reminder_to_send["user_reminder_id"],
                        self.humanize_relativedelta(
                            relativedelta(datetime.fromtimestamp(self.next_reminder_to_send["expires"], timezone.utc), datetime.now(timezone.utc)),
                        ),
                    )
                else:
                    log.debug("There are no more reminders left to send.")

    #
    # Private methods
    #

    async def get_last_seen_channels(self, user_id: int):
        exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
        if not exe or not exe.database:
            msg = "No db"
            raise ValueError(msg)
        return await exe.database.fetch(SEEN_QUERY, str(user_id))

    async def _send_reminder(self, full_reminder: dict) -> None:
        """Send reminders that have expired."""
        delete = False
        user = self.bot.get_user(full_reminder["user_id"])
        if user is None:
            log.debug("User={} is not visible to the bot. Deleting reminder.", full_reminder["user_id"])

        else:
            embed = await self._generate_reminder_embed(user, full_reminder)
            try:
                log.warning("Sending reminder to user={}...", full_reminder["user_id"])
                if await self.bot.redis.exhget("no_dm_users", user.id):
                    msg = "No DM"
                    raise ValueError(msg)
                try:
                    await user.send(embed=embed)
                    delete = True
                except discord.HTTPException:
                    await self.bot.redis.exhset("no_dm_users", user.id, time.time(), ex=86400)
                    raise

            except (ValueError, discord.HTTPException):
                last_channels = await self.get_last_seen_channels(user.id)
                if last_channels:
                    last_channels = [i["channel_id"] for i in last_channels]
                    for cid in last_channels:
                        channel: discord.TextChannel = self.bot.get_channel(int(cid))
                        if not channel:
                            continue
                        if member := channel.guild.get_member(user.id):
                            _a = AsyncIter(channel.members)
                            if await _a.filter(lambda m: m.id == user.id):
                                first_reminder = await self.bot.redis.hget("first_reminders_sent", member.id)
                                if not first_reminder:
                                    await channel.send(f"{member.mention}, I tried to DM you but your dms are off. Your reminder: ", embed=embed)
                                    await self.bot.redis.hset("first_reminders_sent", user.id, time.time())
                                else:
                                    await channel.send(f"{member.mention}", embed=embed)
                                    delete = True

                                log.success("Unable to DM {} but I found them in {} / {}", member, channel, channel.guild)

                                break

            total_sent = await self.config.total_sent()
            await self.config.total_sent.set(total_sent + 1)

        # Get the config for editing
        config_reminder = self.config.custom("REMINDER", str(full_reminder["user_id"]), str(full_reminder["user_reminder_id"]))

        # Handle repeats and deletes
        if not delete and full_reminder["repeat"]:
            # Make sure repeat interval is at least a day
            now = datetime.now(timezone.utc)
            if now + relativedelta(**full_reminder["repeat"]) < now + relativedelta(days=1):
                full_reminder["repeat"] = {"days": 1}
                await config_reminder.repeat.set(full_reminder["repeat"])
            # Calculate next reminder
            next_reminder_time = datetime.fromtimestamp(full_reminder["expires"], timezone.utc)
            repeat_time = relativedelta(**full_reminder["repeat"])
            try:
                while next_reminder_time < now:
                    next_reminder_time = next_reminder_time + repeat_time
                # Set new reminder time
                await config_reminder.created.set(full_reminder["expires"])
                await config_reminder.expires.set(int(next_reminder_time.timestamp()))
            except (OverflowError, ValueError):
                # Next repeat would be after the year 9999. We don't support that.
                await config_reminder.clear()
        else:
            await config_reminder.clear()
        # Search for next reminder, in case this was a successful retry reminder
        self.search_for_next_reminder = True

    async def _generate_reminder_embed(self, user: int, full_reminder: dict):
        """Generate the reminder embed."""
        # Determine any delay
        current_time = datetime.now(timezone.utc)
        current_time_seconds = int(current_time.timestamp())
        delay = current_time_seconds - full_reminder["expires"]
        if delay < self.SEND_DELAY_SECONDS:
            delay = 0
        # Title
        embed = discord.Embed(color=await self.bot.get_embed_color(user))
        # Footer if delay
        if delay:
            embed.set_footer(
                text=f"This was supposed to send {self.humanize_relativedelta(relativedelta(seconds=delay))} ago.\nI might be having network or server issues, or perhaps I just started up.\nSorry about that!",
            )
        # Field name
        field_name = f":bell:{' (Delayed)' if delay else ''}{' Repeating' if full_reminder['repeat'] else ''} Reminder! :bell:"
        # Field value - time ago
        if full_reminder["repeat"]:
            field_value = f"Every {self.humanize_relativedelta(full_reminder['repeat'])}:"
        else:
            if delay:
                time_ago = self.humanize_relativedelta(relativedelta(current_time, datetime.fromtimestamp(full_reminder["created"], timezone.utc)))
            else:
                time_ago = self.humanize_relativedelta(
                    relativedelta(
                        datetime.fromtimestamp(full_reminder["expires"], timezone.utc),
                        datetime.fromtimestamp(full_reminder["created"], timezone.utc),
                    ),
                )
            field_value = f"From {time_ago} ago:"
        # Field value - reminder text
        field_value += f"\n\n{full_reminder['text']}"
        if len(field_value) > 800:
            field_value = f"{field_value[:797]}..."
        # Field value - jump link and timestamp
        footer_part = ""
        if full_reminder["jump_link"]:
            footer_part = f"[original message]({full_reminder['jump_link']})"
        if not full_reminder["repeat"]:
            if footer_part:
                footer_part += " • "
            footer_part += f"<t:{full_reminder['created']}:f>"
        if footer_part:
            field_value += f"\n\n{footer_part}"

        embed.add_field(name=field_name, value=field_value)
        return embed

    def _get_full_reminder_from_partial(self, user_id: int, user_reminder_id: int, partial_reminder: dict):
        """Construct a full reminder from a partial reminder.

        This reminder object will be the same as the partial_reminder
        passed in, except that it will include the user_id, the
        user_reminder_id, as well as any missing defaults (such as
        repeat).

        DO NOT SAVE THIS BACK TO THE CONFIG! Doing so would be a waste
        of disk space. Only save back specific modified values (and
        never user_id nor user_reminder_id).

        """
        result = self.config.custom("REMINDER", str(user_id), str(user_reminder_id)).nested_update(partial_reminder)
        result.update({"user_id": user_id, "user_reminder_id": user_reminder_id})
        return result

    #
    # Public methods
    #

    @staticmethod
    def humanize_relativedelta(relative_delta: Union[relativedelta, dict]):
        """Convert relativedelta (or a dict of its keyword arguments) into a
        humanized string.
        """
        if isinstance(relative_delta, dict):
            relative_delta = relativedelta(**relative_delta)
        periods = [
            ("year", "years", relative_delta.years),
            ("month", "months", relative_delta.months),
            ("week", "weeks", relative_delta.weeks),
            ("day", "days", relative_delta.days % 7),
            ("hour", "hours", relative_delta.hours),
            ("minute", "minutes", relative_delta.minutes),
            ("second", "seconds", relative_delta.seconds),
        ]

        strings = []
        for period_name, plural_period_name, time_unit in periods:
            if time_unit == 0:
                continue
            unit = plural_period_name if time_unit not in (1, -1) else period_name
            strings.append(f"{time_unit} {unit}")

        if not strings:
            strings.append("0 seconds")
        return humanize_list(strings)

    async def insert_reminder(self, user_id: int, reminder: dict) -> bool:
        """Insert a new reminder into the config.

        Will handle generating a user_reminder_id and reminder limits.
        Returns True for success, False for user having too many
        reminders.

        """
        # Check that the user has room for another reminder
        maximum = await self.config.max_user_reminders()
        users_partial_reminders = await self.config.custom("REMINDER", str(user_id)).all()  # Does NOT return default values
        if len(users_partial_reminders) > maximum - 1:
            return False

        # Get next user_reminder_id
        next_reminder_id = 1
        while str(next_reminder_id) in users_partial_reminders:  # Keys are strings
            next_reminder_id += 1

        # Save new reminder
        await self.config.custom("REMINDER", str(user_id), str(next_reminder_id)).set(reminder)

        # Update background task
        await self.update_bg_task(user_id, next_reminder_id, reminder)
        return True

    @staticmethod
    def relativedelta_to_dict(relative_delta: relativedelta) -> dict[str, int]:
        """Convert a relativedelta to a dict representation (for storing)."""
        periods = [
            ("years", relative_delta.years),
            ("months", relative_delta.months),
            ("days", relative_delta.days),
            ("hours", relative_delta.hours),
            ("minutes", relative_delta.minutes),
            ("seconds", relative_delta.seconds),
        ]
        return {key: value for key, value in periods if value != 0}

    async def send_too_many_message(self, ctx_or_user: Union[commands.Context, discord.User], maximum: int = -1) -> None:
        """Send a message to the user telling them they have too many reminders."""
        if maximum < 0:
            maximum = await self.config.max_user_reminders()
        plural = "reminder" if maximum == 1 else "reminders"
        message = f"You have too many reminders! I can only keep track of {maximum} {plural} for you at a time."
        if isinstance(ctx_or_user, commands.Context):
            await reply(ctx_or_user, message)
        else:
            await ctx_or_user.send(message)

    async def update_bg_task(self, user_id: int, user_reminder_id: int = None, partial_reminder: dict = None) -> None:
        """Request the background task to consider a new (or updated) reminder.

        user_id is always required, user_reminder_id and
        partial_reminder are usually required, unless we are doing
        reminder deletions

        """
        if self.search_for_next_reminder:
            # If the bg task is already going to perform a search soon
            log.debug("Background task will be searching for new reminders soon")
            return
        elif not self.next_reminder_to_send:
            # If the bg task isn't waiting on any reminders currently
            self.search_for_next_reminder = True
            log.debug("Background task has no reminders, forcing search")
        elif not user_reminder_id and self.next_reminder_to_send["user_id"] == user_id:
            # If there isn't a user_reminder_id, the user must have deleted all of their reminders
            self.search_for_next_reminder = True
            log.debug("Background task reminder user deleted all their reminders, forcing search")
        elif self.next_reminder_to_send["user_id"] == user_id and self.next_reminder_to_send["user_reminder_id"] == user_reminder_id:
            # If the modified reminder is the one the bg task is going to send next
            self.search_for_next_reminder = True
            log.debug("Modified background task reminder, forcing search")
        elif partial_reminder and self.next_reminder_to_send["expires"] > partial_reminder["expires"]:
            # If the new reminder expires sooner than the current next reminder
            self.search_for_next_reminder = True
            log.debug("New reminder expires before background task reminder, forcing search")
        elif user_reminder_id and self.problematic_reminders:
            # Check if the new reminder is currently being retried
            for reminder in self.problematic_reminders:
                if reminder["user_id"] == user_id and reminder["user_reminder_id"] == user_reminder_id:
                    self.search_for_next_reminder = True
                    log.debug("Modified reminder is in the retry queue, forcing search")
                    break
