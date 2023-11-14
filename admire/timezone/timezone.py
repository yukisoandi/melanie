from __future__ import annotations

from datetime import datetime
from typing import Optional

import arrow
import discord
import pytz
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from executionstracker.exe import ExecutionsTracker
from melanie.helpers import make_e
from melanie.timezonekit.utils import match_fuzzy

__version__ = "2.1.1"


def get_time_emoji(date: arrow.Arrow):
    if date.hour >= 5 and date.hour <= 12:
        return "☀️"

    return "🌇" if date.hour > 12 and date.hour <= 17 else "🌚"


Q = """select timezone
from timezonedata
where name  ilike $1 ;"""


class Timezone(commands.Cog):
    """Gets times across the world..."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 278049241001, force_registration=True)
        default_user = {"usertime": None}
        self.config.register_user(**default_user)

    async def query_db(self, value):
        exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
        return await exe.database.fetchval(Q, value)

    async def get_usertime(self, user: discord.User):
        usertime = await self.config.user(user).usertime()
        tz = pytz.timezone(usertime) if usertime else None
        return usertime, tz

    async def format_results(self, ctx: commands.Context, tz):
        if not tz:
            embed = make_e(
                "I wasnt able to match that to a timezone. Use a major city such as 'Chicago' or provide the name of the timezone (CST, Central, etc)",
                3,
            )
            await ctx.send(embed=embed)
            return None
        elif len(tz) == 1:
            # command specific response, so don't do anything here
            return tz
        else:
            tzs = "\n".join(tz)
            embed = make_e(f"This matched multiple timezones. Please be more specific!\n\n{tzs}", 2)
            await ctx.send(embed=embed)
            return None

    @commands.guild_only()
    @commands.group(invoke_without_command=True, aliases=["time"])
    async def tz(self, ctx: commands.Context, user: Optional[discord.User]):
        """Get someone's current time."""
        if not user:
            usertime = await self.config.user(ctx.author).usertime()
            if not usertime:
                return await ctx.send(embed=make_e("Your timezone is not set", tip="configure it with ;tz set <timezone>", status=2))
            emote = get_time_emoji(arrow.now(usertime))
            ts = arrow.now(usertime).format("h:m A")
            return await ctx.send(embed=make_e(f"{emote} Your time is currently {ts}", status="info"))

        else:
            usertime = await self.config.user(user).usertime()
            if not usertime:
                return await ctx.send(embed=make_e(f"**{user}'s** timezone is not set", tip="tell them configure it with ;tz set <timezone>", status=2))
            ts = arrow.now(usertime).format("h:m A")
            emote = get_time_emoji(arrow.now(usertime))
            return await ctx.send(embed=make_e(f"{emote} **{user}'s** time is currently {ts}", status="info"))

    @tz.command(name="set")
    async def tzset(self, ctx: commands.Context, *, location: str):
        """Sets your timezone.
        Usage: ;time me Continent/City
        Using the command with no timezone will show your current timezone, if any.
        """
        search = match_fuzzy(location)
        if not search:
            search = await self.query_db(location)
            if search:
                search = match_fuzzy(search)
        if not search:
            return await self.format_results(ctx, search)
        if isinstance(search, list):
            search = search[0]
        await self.config.user(ctx.author).usertime.set(search)
        return await ctx.send(embed=make_e(f"Your timezone was set to **{search.replace('_',' ')} ** "))

    @tz.command()
    async def compare(self, ctx: commands.Context, user: discord.Member = None):
        """Compare your saved timezone with another user's timezone."""
        if not user:
            return await ctx.send_help()
        pytz.all_timezones

        usertime, user_tz = await self.get_usertime(ctx.author)
        othertime, other_tz = await self.get_usertime(user)

        if not usertime:
            return await ctx.send(
                f"You haven't set your timezone. Do `{ctx.prefix}time me Continent/City`: see <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>",
            )
        if not othertime:
            return await ctx.send("That user's timezone isn't set yet.")

        user_now = datetime.now(user_tz)
        user_diff = user_now.utcoffset().total_seconds() / 60 / 60
        other_now = datetime.now(other_tz)
        other_diff = other_now.utcoffset().total_seconds() / 60 / 60
        time_diff = abs(user_diff - other_diff)
        time_diff_text = f"{time_diff:g}"
        fmt = "**%H:%M %Z (UTC %z)**"
        other_time = other_now.strftime(fmt)
        plural = "" if time_diff_text == "1" else "s"
        time_amt = "the same time zone as you" if time_diff_text == "0" else f"{time_diff_text} hour{plural}"
        position = "ahead of" if user_diff < other_diff else "behind"
        position_text = "" if time_diff_text == "0" else f" {position} you"

        await ctx.send(f"{user.display_name}'s time is {other_time} which is {time_amt}{position_text}.")
