"""Commands for the average user."""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import arrow
import discord
import orjson
import parsedatetime as pdt
import regex as re
import unidecode
from async_lru import alru_cache
from dateutil.relativedelta import relativedelta
from humanize import naturaldelta
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import error
from melaniebot.core.utils.predicates import MessagePredicate
from pyparsing import ParseException

from chatgpt.models import ChatResponse
from melanie import get_curl, log, make_e
from remindme.abc import MixinMeta
from remindme.pcx_lib import delete, reply


@alru_cache(ttl=60)
async def parse_via_openai(text: str) -> ChatResponse:
    text = " ".join(unidecode.unidecode(text).strip().split())[:500]

    payload = {
        "model": "gpt-3.5-turbo-16k-0613",
        "messages": [
            {"role": "system", "content": f'Answer all the questions with a single integer. Here is the sample text: \n\n"{text}"\n'},
            {"role": "user", "content": "from the text, how  many seconds from now should the action be done for the first time?"},
        ],
        "temperature": 0.9,
        "max_tokens": 150,
        "top_p": 1,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }
    curl = get_curl()

    r = await curl.fetch(
        "https://api.openai.com/v1/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.getenv("OPENAI_API_KEY", "")},
        body=orjson.dumps(payload),
        method="POST",
    )
    return ChatResponse.parse_raw(r.body)


class ReminderCommands(MixinMeta):
    """Commands for the average user."""

    cal: pdt.Calendar
    bot: Melanie

    @commands.group(pass_context=True, invoke_without_command=True)
    async def reminder(self, ctx: commands.Context, *, what_and_when: Optional[str]) -> None:
        """Manage your reminders."""
        if not what_and_when:
            return await ctx.send_help()

        return await self._create_reminder(ctx, what_and_when)

    @reminder.command(aliases=["get"])
    async def list(self, ctx: commands.Context, sort: str = "time") -> None:
        """Show a list of all of your reminders.

        Sort can either be: `time` (default) for soonest expiring
        reminder first, `added` for ordering by when the reminder was
        added, `id` for ordering by ID

        """
        # Grab users reminders, format them so we can see the user_reminder_id
        author = ctx.message.author
        user_reminders = []
        user_reminders_dict = await self.config.custom("REMINDER", str(author.id)).all()  # Does NOT return default values
        for user_reminder_id, reminder in user_reminders_dict.items():
            reminder.update({"user_reminder_id": user_reminder_id})
            user_reminders.append(reminder)

        # Check if they actually have any reminders
        if not user_reminders:
            await reply(ctx, "You don't have any upcoming reminders.")
            return

        # Sort the reminders
        if sort == "added":
            pass
        elif sort == "id":
            user_reminders.sort(key=lambda reminder_info: reminder_info["user_reminder_id"])
        elif sort == "time":
            user_reminders.sort(key=lambda reminder_info: reminder_info["expires"])
        else:
            await reply(ctx, "That is not a valid sorting option. Choose from `time` (default), `added`, or `id`.")
            return

        # Make a pretty embed listing the reminders
        embed = discord.Embed(title=f"Reminders for {author.display_name}", color=await ctx.embed_color())
        embed.set_thumbnail(url=author.avatar_url)
        for reminder in user_reminders:  # TODO make this look nicer.
            reminder_title = f"ID# {reminder['user_reminder_id']} — <t:{reminder['expires']}:f>"
            if "repeat" in reminder and reminder["repeat"]:
                reminder_title += f", repeating every {self.humanize_relativedelta(reminder['repeat'])}"
            reminder_text = reminder["text"]
            if reminder["jump_link"]:
                reminder_text += f"\n([original message]({reminder['jump_link']}))"
            reminder_text = reminder_text or "(no reminder text or jump link)"
            embed.add_field(name=reminder_title, value=reminder_text, inline=False)
        await ctx.send(embed=embed)

    @reminder.command(aliases=["add"])
    async def create(self, ctx: commands.Context, *, what_and_when: str = "") -> None:
        """Create a reminder with optional reminder text.

        Same as `;remindme`, so check that for usage help.

        """
        await self._create_reminder(ctx, what_and_when)

    @reminder.group(aliases=["edit"])
    async def modify(self, ctx: commands.Context) -> None:
        """Modify an existing reminder."""

    @modify.command()
    async def time(self, ctx: commands.Context, reminder_id: int, *, time: str) -> None:
        """Modify the time of an existing reminder."""
        config_reminder = await self._get_reminder_config_group(ctx, ctx.message.author.id, reminder_id)
        if not config_reminder:
            return

        # Parse users reminder time and text
        parse_result = await self._parse_time_text(ctx, time, validate_text=False)
        if not parse_result:
            return

        # Save new values
        await config_reminder.created.set(parse_result["created_timestamp_int"])
        await config_reminder.expires.set(parse_result["expires_timestamp_int"])
        if parse_result["repeat_delta"]:
            await config_reminder.repeat.set(self.relativedelta_to_dict(parse_result["repeat_delta"]))

        # Notify background task
        await self.update_bg_task(ctx.message.author.id, reminder_id, await config_reminder.all())

        # Pull repeat dict from config in case we didn't update it
        repeat_dict = await config_reminder.repeat()
        # Send confirmation message
        message = f"Reminder with ID# **{reminder_id}** will remind you in {self.humanize_relativedelta(parse_result['expires_delta'])} from now (<t:{parse_result['expires_timestamp_int']}:f>)"
        if repeat_dict:
            message += f", repeating every {self.humanize_relativedelta(repeat_dict)} thereafter."
        else:
            message += "."
        await reply(ctx, message)

    @modify.command()
    async def repeat(self, ctx: commands.Context, reminder_id: int, *, time: str) -> None:
        """Modify the repeating time of an existing reminder.

        Pass "0" to <time> in order to disable repeating.

        """
        config_reminder = await self._get_reminder_config_group(ctx, ctx.message.author.id, reminder_id)
        if not config_reminder:
            return

        # Check for repeat cancel
        if time.lower() in {"0", "stop", "none", "false", "no", "cancel", "n"}:
            await config_reminder.repeat.clear()
            await reply(
                ctx,
                f"Reminder with ID# **{reminder_id}** will not repeat anymore. The final reminder will be sent <t:{await config_reminder.expires()}:f>.",
            )
        else:
            # Parse users reminder time and text
            parse_result = await self._parse_time_text(ctx, time, validate_text=False)
            if not parse_result:
                return

            # Save new value
            await config_reminder.repeat.set(self.relativedelta_to_dict(parse_result["expires_delta"]))

            await reply(
                ctx,
                f"Reminder with ID# **{reminder_id}** will now remind you every {self.humanize_relativedelta(parse_result['expires_delta'])}, with the first reminder being sent <t:{await config_reminder.expires()}:f>.",
            )

    @modify.command()
    async def text(self, ctx: commands.Context, reminder_id: int, *, text: str) -> None:
        """Modify the text of an existing reminder."""
        config_reminder = await self._get_reminder_config_group(ctx, ctx.message.author.id, reminder_id)
        if not config_reminder:
            return

        text = text.strip()
        if len(text) > 800:
            await reply(ctx, "Your reminder text is too long.")
            return

        await config_reminder.text.set(text)
        await reply(ctx, f"Reminder with ID# **{reminder_id}** has been edited successfully.")

    @reminder.command(aliases=["delete", "del"])
    async def remove(self, ctx: commands.Context, index: str) -> None:
        """Delete a reminder.

        <index> can either be:
        - a number for a specific reminder to delete
        - `last` to delete the most recently created reminder
        - `all` to delete all reminders (same as ;forgetme)

        """
        await self._delete_reminder(ctx, index)

    @commands.command(aliases=["remind"])
    async def remindme(self, ctx: commands.Context, *, what_and_when: str = "") -> None:
        """Create a reminder with optional reminder text.

        Examples
        --------
        `;remindme in 8min45sec to do that thing`
        `;remindme to water my plants in 2 hours`
        `;remindme in 3 days`
        `;remindme 8h`
        `;remindme every 1 week to take out the trash`
        `;remindme in 1 hour to drink some water every 1 day`

        """
        if not what_and_when:
            return await ctx.send_help()
        await self._create_reminder(ctx, what_and_when)

    @commands.command()
    async def forgetme(self, ctx: commands.Context) -> None:
        """Remove all of your upcoming reminders."""
        await self._delete_reminder(ctx, "all")

    @reminder.command(name="forgetme")
    async def forgetme2(self, ctx: commands.Context) -> None:
        """Delete all your reminders."""
        return await self.forgetme(ctx=ctx)

    async def _create_reminder(self, ctx: commands.Context, what_and_when: str) -> None:
        """Logic to create a reminder."""
        # Check that user is allowed to make a new reminder
        what_and_when = " ".join(what_and_when.strip().split())
        str(what_and_when)
        async with ctx.typing():
            async with asyncio.timeout(20):
                author = ctx.message.author
                maximum = await self.config.max_user_reminders()
                users_reminders = await self.config.custom("REMINDER", str(author.id)).all()  # Does NOT return default values
                if len(users_reminders) > maximum - 1:
                    return await self.send_too_many_message(ctx, maximum)
                # Parse users reminder time and text
                search = self.cal.nlp(what_and_when, sourceTime=arrow.utcnow().datetime.timetuple())
                if not search:
                    ask = await parse_via_openai(ctx.message.content)
                    ask = ask.choices[0].message.content
                    with contextlib.suppress(ValueError):
                        delta = int(ask)

                        search = self.cal.nlp(f"{delta} seconds", sourceTime=arrow.utcnow().datetime.timetuple())
                    if search:
                        log.success("Fallback to ChatGPT OK. Value: {} Result: {}", what_and_when, ask)
                if not search:
                    return await ctx.send(embed=make_e("I couldn't parse a date from that reminder.. try something a bit more specific, please.", 3))
                _datetime, flags, start, end, timestring = search[0]
                datetime, dctx = self.cal.parse(timestring, sourceTime=arrow.utcnow().datetime.timetuple())
                expire = arrow.get(datetime)
                repeat_delta = None

                expire_ts = int(expire.timestamp())

                expire_ts += 10 if expire_ts < 10 else 1
                what_and_when = " ".join(what_and_when.split())

                if "every" in what_and_when[:end]:
                    _dt = int(expire_ts - time.time())
                    if _dt < 900:
                        return await ctx.send(embed=make_e("Repeating reminders must be at least 30 minutes apart.", 3))
                    repeat_delta = relativedelta(seconds=_dt)

                what_and_when = re.sub(".*to", "", what_and_when)
                remainder_txt = what_and_when.replace(timestring, "")

                if not remainder_txt:
                    return await ctx.send("remind you of what...")
                remainder_txt = " ".join(remainder_txt.split())
                # if not gpt_parsed:

                parse_result = {
                    # Always present, never None
                    "created_timestamp_int": int(time.time()),
                    "expires_delta": int(expire_ts - time.time()),
                    "expires_timestamp_int": expire_ts,
                    "reminder_text": remainder_txt,
                    "repeat_delta": repeat_delta,
                }

                new_reminder = {
                    "text": parse_result["reminder_text"],
                    "created": parse_result["created_timestamp_int"],
                    "expires": parse_result["expires_timestamp_int"],
                    "jump_link": ctx.message.jump_url,
                }

                # Check for repeating reminder
                if parse_result["repeat_delta"]:
                    new_reminder["repeat"] = self.relativedelta_to_dict(parse_result["repeat_delta"])

                # Save reminder for user (also handles notifying background task)
                if not await self.insert_reminder(author.id, new_reminder):
                    return await self.send_too_many_message(ctx, maximum)

                # Let user know we successfully saved their reminder
                message = f"I will remind you of {'that' if parse_result['reminder_text'] else 'this'} "
                if parse_result["repeat_delta"]:
                    message += f"every {naturaldelta(parse_result['expires_delta'], minimum_unit='seconds').replace('an ',   '')}"
                else:
                    message += f"in {naturaldelta(parse_result['expires_delta'],minimum_unit='seconds')} (<t:{parse_result['expires_timestamp_int']}:f>)"
                if parse_result["repeat_delta"] and parse_result["expires_delta"] != parse_result["repeat_delta"]:
                    message += f", with the first reminder in {naturaldelta(parse_result['expires_delta'],minimum_unit='seconds')} (<t:{parse_result['expires_timestamp_int']}:f>)."
                else:
                    message += "."
                await reply(ctx, message)

                # Send me too message if enabled
                if ctx.guild and await self.config.guild(ctx.guild).me_too() and ctx.channel.permissions_for(ctx.me).add_reactions:
                    query: discord.Message = await ctx.send(
                        f"If anyone else would like {'these reminders' if parse_result['repeat_delta'] else 'to be reminded'} as well, click the bell below!",
                    )
                    self.me_too_reminders[query.id] = new_reminder
                    self.clicked_me_too_reminder[query.id] = {author.id}
                    await query.add_reaction(self.reminder_emoji)
                    await asyncio.sleep(30)
                    await delete(query)
                    del self.me_too_reminders[query.id]
                    del self.clicked_me_too_reminder[query.id]

    async def _delete_reminder(self, ctx: commands.Context, index: str) -> None:
        """Logic to delete reminders."""
        if not index:
            return
        author = ctx.message.author

        if index == "all":
            all_users_reminders = self.config.custom("REMINDER", str(author.id))
            if not await all_users_reminders.all():
                await reply(ctx, "You don't have any upcoming reminders.")
                return

            # Ask if the user really wants to do this
            pred = MessagePredicate.yes_or_no(ctx)
            await reply(ctx, "Are you **sure** you want to remove all of your reminders? (yes/no)")
            with contextlib.suppress(asyncio.TimeoutError):
                await ctx.bot.wait_for("message", check=pred, timeout=30)
            if not pred.result:
                await reply(ctx, "I have left your reminders alone.")
                return
            await all_users_reminders.clear()
            # Notify background task
            await self.update_bg_task(author.id)
            await reply(ctx, "All of your reminders have been removed.")
            return

        if index == "last":
            all_users_reminders_dict = await self.config.custom("REMINDER", str(author.id)).all()
            if not all_users_reminders_dict:
                await reply(ctx, "You don't have any upcoming reminders.")
                return

            reminder_id_to_delete = list(all_users_reminders_dict)[-1]
            await self.config.custom("REMINDER", str(author.id), reminder_id_to_delete).clear()
            # Notify background task
            await self.update_bg_task(author.id, reminder_id_to_delete)
            await reply(ctx, f"Your most recently created reminder (ID# **{reminder_id_to_delete}**) has been removed.")
            return

        try:
            int_index = int(index)
        except ValueError:
            await ctx.send_help()
            return

        config_reminder = await self._get_reminder_config_group(ctx, author.id, int_index)
        if not config_reminder:
            return
        await config_reminder.clear()
        # Notify background task
        await self.update_bg_task(author.id, int_index)
        await reply(ctx, f"Reminder with ID# **{int_index}** has been removed.")

    async def _get_reminder_config_group(self, ctx: commands.Context, user_id: int, user_reminder_id: int):
        config_reminder = self.config.custom("REMINDER", str(user_id), str(user_reminder_id))
        if not await config_reminder.expires():
            await reply(ctx, f"Reminder with ID# **{user_reminder_id}** does not exist! Check the reminder list and verify you typed the correct ID#.")
            return None
        return config_reminder

    async def _parse_time_text(self, ctx: commands.Context, what_and_when: str, validate_text: bool = True) -> Optional[dict[str, Any]]:
        try:
            parse_result = self.reminder_parser.parse(what_and_when.strip())
        except ParseException:
            await reply(ctx, error("I couldn't understand the format of your reminder time and text."))
            return None

        created_datetime = datetime.now(timezone.utc)
        created_timestamp_int = int(created_datetime.timestamp())

        repeat_dict = parse_result["every"] if "every" in parse_result else None
        repeat_delta = None
        if repeat_dict:
            repeat_delta = relativedelta(**repeat_dict)
            try:
                # Make sure repeat isn't really big or less than 1 day
                if created_datetime + repeat_delta < created_datetime + relativedelta(days=1):
                    await reply(ctx, "Reminder repeat time must be at least 1 day.")
                    return None
            except (OverflowError, ValueError):
                await reply(ctx, "Reminder repeat time is too large.")
                return None

        expires_dict = parse_result["in"] if "in" in parse_result else repeat_dict
        if not expires_dict:
            await ctx.send_help()
            return None
        expires_delta = relativedelta(**expires_dict)
        try:
            # Make sure expire time isn't over 9999 years and is at least 1 minute
            if created_datetime + expires_delta < created_datetime + relativedelta(minutes=1):
                await reply(ctx, "Reminder time must be at least 1 minute.")
                return None
        except (OverflowError, ValueError):
            await reply(ctx, "Reminder time is too large.")
            return None
        expires_datetime = created_datetime + expires_delta
        expires_timestamp_int = int(expires_datetime.timestamp())

        reminder_text = parse_result["text"] if "text" in parse_result else ""
        if validate_text and len(reminder_text) > 800:
            await reply(ctx, "Your reminder text is too long.")
            return None

        return {
            # Always present, never None
            "created_timestamp_int": created_timestamp_int,
            "expires_delta": expires_delta,
            "expires_timestamp_int": expires_timestamp_int,
            # Optional, could be None/empty string
            "reminder_text": reminder_text,
            "repeat_delta": repeat_delta,
        }
