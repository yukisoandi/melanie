from __future__ import annotations

import asyncio
import contextlib
import time as _time
from abc import ABC
from asyncio import TimeoutError as AsyncTimeoutError
from datetime import datetime, timedelta
from typing import Optional, Union

import arrow
import discord
import humanize
import regex as re
from aiomisc.backoff import asyncretry
from boltons.timeutils import parse_timedelta
from discord.ext.commands import MemberConverter, MemberNotFound
from discord.http import Route
from humanize.time import precisedelta
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.commands.converter import TimedeltaConverter
from melaniebot.core.utils import menus, mod, predicates
from melaniebot.core.utils.chat_formatting import escape, pagify, text_to_file
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from tornado.ioloop import IOLoop

from antinuke.antinuke import AntiNuke, AntinukeLimitSurpassed
from melanie import create_task, make_e
from melanie.vendor.disputils import BotConfirmation

from . import errors
from .api import API, UnavailableMember, check_ban_key
from .automod import AutomodMixin
from .cache import MemoryCache
from .converters import AdvancedMemberSelect
from .settings import SettingsMixin


def _(x):
    return x


BaseCog = getattr(commands, "Cog", object)

# Melanie 3.0 backwards compatibility, thanks Sinbad
listener = getattr(commands.Cog, "listener", None)
if listener is None:

    def listener(name=None):
        return lambda x: x


class DiscordTimeoutError(Exception):
    pass


async def set_timeout(ctx: commands.Context, guild_id: int, user_id: int, duration: Optional[arrow.Arrow]):
    class V9Route(Route):
        BASE: str = "https://discord.com/api/v9"

    guild: discord.Guild = ctx.cog.bot.get_guild(guild_id)
    me: discord.Member = guild.me
    member: discord.Member = guild.get_member(user_id)

    if duration:
        if member.guild_permissions.administrator:
            msg = "Cannot timeout administrators"
            raise DiscordTimeoutError(msg)

        if member == guild.owner:
            msg = "Cannot timeout the guild owner"
            raise DiscordTimeoutError(msg)

        if me.top_role <= member.top_role:
            msg = "My top role must be above the members top role to set timeout"
            raise DiscordTimeoutError(msg)

    duration = duration.isoformat() if duration else None
    payload = {"communication_disabled_until": duration}

    route = V9Route("PATCH", "/guilds/{guild_id}/members/{user_id}", guild_id=guild_id, user_id=user_id)

    try:
        return await ctx.cog.bot.http.request(route, json=payload)
    except discord.errors.Forbidden as e:
        msg = "Missing permissions"
        raise DiscordTimeoutError(msg) from e


async def is_timedout(ctx: commands.Context, guild_id: int, user_id: int):
    class V9Route(Route):
        BASE: str = "https://discord.com/api/v9"

    async with asyncio.timeout(5):
        route = V9Route("GET", "/guilds/{guild_id}/members/{user_id}", guild_id=guild_id, user_id=user_id)
        r = await ctx.cog.bot.http.request(route)
        return bool(r["communication_disabled_until"])


def pretty_date(time: datetime):
    """Get a datetime object and return a pretty string like 'an hour ago',
    'Yesterday', '3 months ago', 'just now', etc.
    """

    def text(amount: float, unit: tuple):
        amount = round(amount)
        unit = unit[1] if amount > 1 else unit[0]
        return f"{amount} {unit} ago."

    units_name = {
        0: ("year", "years"),
        1: ("month", "months"),
        2: ("week", "weeks"),
        3: ("day", "days"),
        4: ("hour", "hours"),
        5: ("minute", "minutes"),
        6: ("second", "seconds"),
    }
    now = datetime.now()
    diff = now - time
    second_diff = diff.seconds
    day_diff = diff.days
    if day_diff < 0:
        return ""
    if day_diff == 0:
        if second_diff < 10:
            return "Just now"
        if second_diff < 60:
            return text(second_diff, units_name[6])
        if second_diff < 120:
            return "A minute ago"
        if second_diff < 3600:
            return text(second_diff / 60, units_name[5])
        if second_diff < 7200:
            return "An hour ago"
        if second_diff < 86400:
            return text(second_diff / 3600, units_name[4])
    if day_diff == 1:
        return "Yesterday"
    if day_diff < 7:
        return text(day_diff, units_name[3])
    if day_diff < 31:
        return text(day_diff / 7, units_name[2])
    if day_diff < 365:
        return text(day_diff / 30, units_name[1])
    return text(day_diff / 365, units_name[0])


def EMBED_MODLOG(x: int):
    if x == 1:
        _ = "A member was warned."
    elif x == 2:
        _ = "A member was muted."
    elif x == 3:
        _ = "A member was kicked."
    elif x == 4:
        _ = "A member was softbanned."
    elif x == 5:
        _ = "A member was banned."
    return _


def EMBED_USER(x: int):
    if x == 1:
        _ = "The moderation team warned you."
    elif x == 2:
        _ = "The moderation team muted you."
    elif x == 3:
        _ = "The moderation team kicked you."
    elif x == 4:
        _ = "The moderation team softbanned you."
    elif x == 5:
        _ = "The moderation team banned you."
    return _


class CompositeMetaClass(type(commands.Cog), type(ABC)):
    """This allows the metaclass used for proper type detection to coexist with
    discord.py's metaclass.
    """


class WarnSystem(SettingsMixin, AutomodMixin, BaseCog, metaclass=CompositeMetaClass):
    """Providing a system of moderation similar to Dyno."""

    default_global = {"data_version": "0.0"}  # will be edited after config update, current version is 1.0
    default_guild = {
        "force_reason": False,
        "delete_message": False,
        "show_mod": True,
        "mute_role": None,
        "update_mute": True,
        "remove_roles": False,
        "respect_hierarchy": True,
        "reinvite": True,
        "log_manual": True,
        "channels": {"main": None, "1": None, "2": None, "3": None, "4": None, "5": None},
        "bandays": {"softban": 7, "ban": 7},
        "embed_description_modlog": {"1": EMBED_MODLOG(1), "2": EMBED_MODLOG(2), "3": EMBED_MODLOG(3), "4": EMBED_MODLOG(4), "5": EMBED_MODLOG(5)},
        "embed_description_user": {"1": EMBED_USER(1), "2": EMBED_USER(2), "3": EMBED_USER(3), "4": EMBED_USER(4), "5": EMBED_USER(5)},
        "substitutions": {},
        "thumbnails": {
            "1": "https://i.imgur.com/Bl62rGd.png",
            "2": "https://i.imgur.com/cVtzp1M.png",
            "3": "https://i.imgur.com/uhrYzyt.png",
            "4": "https://i.imgur.com/uhrYzyt.png",
            "5": "https://cdn.discordapp.com/attachments/901386311586439179/901386800864563240/point_laugh.png",
        },
        "colors": {"1": 0xF4AA42, "2": 0xD1ED35, "3": 0xED9735, "4": 0xED6F35, "5": 0xFF4C4C},
        "url": None,
        "temporary_warns": {},
        "automod": {
            "enabled": False,
            "antispam": {
                "enabled": False,
                "max_messages": 5,
                "delay": 2,
                "delay_before_action": 60,
                "warn": {"level": 1, "reason": "Sending messages too fast!", "time": None},
            },
            "regex": {},
            "warnings": [],
        },
    }
    default_custom_member = {"x": []}

    def __init__(self, bot) -> None:
        self.bot = bot

        self.data = Config.get_conf(self, 260, force_registration=True)
        self.dask = bot.dask
        self.data.register_global(**self.default_global)
        self.data.register_guild(**self.default_guild)
        with contextlib.suppress(AttributeError):
            self.data.init_custom("MODLOGS", 2)
        self.data.register_custom("MODLOGS", **self.default_custom_member)

        self.cache = MemoryCache(self.bot, self.data)
        self.api = API(self.bot, self.data, self.cache)

        self.task: asyncio.Task

    __version__ = "1.3.19"

    async def call_warn(self, ctx: commands.Context, level: int, member: discord.Member, reason=None, time=None):
        """No need to repeat, let's do what's common to all 5 warnings."""
        ioloop = IOLoop.current()

        react_lock = asyncio.Lock()
        force_reason = await self.data.guild(ctx.guild).force_reason()
        if force_reason and reason is None:
            return await ctx.send(embed=make_e("This server requires a reason for all moderation actions. Please provide one.", status=3))

        if isinstance(member, discord.Member) and level >= 3 and member.premium_since:
            confirmation = BotConfirmation(ctx, 0xFF5555)
            if level == 3:
                action = "kick"
            elif level == 4:
                action = "softban"
            elif level == 5:
                action = "ban"
            msg = f"{member} is currently boosting the server!"
            await confirmation.confirm(msg, description=f"Are you sure you want to {action} them?", hide_author=True)
            if confirmation.confirmed:
                await confirmation.update("Member removal confirmed.", color=0x55FF55)
            else:
                return await confirmation.update("Action cancelled.", hide_author=True, color=0xFF5555)

        @asyncretry(max_tries=3, pause=1)
        async def react():
            async with react_lock:
                with contextlib.suppress(discord.errors.NotFound):
                    await ctx.message.add_reaction("✨")

        ioloop.add_callback(react)
        reason = await self.api.format_reason(ctx.guild, reason)
        if reason:
            reason = reason[:2000]

        try:
            fail = await self.api.warn(ctx.guild, [member], ctx.author, level, reason, time)
            if fail:
                raise fail[0]
        except errors.MissingPermissions as e:
            await ctx.send(embed=make_e(str(e), status=3))
        except errors.MemberTooHigh as e:
            await ctx.send(embed=make_e(str(e), status=3))
        except errors.LostPermissions as e:
            await ctx.send(embed=make_e(str(e), status=3))
        except errors.SuicidePrevention as e:
            await ctx.send(embed=make_e(str(e), status=3))
        except errors.MissingMuteRole:
            await ctx.send(embed=make_e("You need to set up the mute role before doing this.\nUse the `;warnset mute` command for this.", status=3))
        except errors.NotFound:
            await ctx.send(embed=make_e("Please set up a modlog channel before warning a member.\n\n*Use the `;warnset channel` command.*\n\n", status=3))
        except errors.NotAllowedByHierarchy:
            msg = f"You are not allowed to do this. {member.mention} is higher than you in the role hierarchy. You can only moderate members whos top role is lower than yours."
            await ctx.send(embed=make_e(msg, status=3))
        except discord.errors.NotFound:
            await ctx.send(embed=make_e("Hackban failed: No user found.", status=3))
        else:

            @asyncretry(max_tries=3, pause=1)
            async def react2():
                async with react_lock:
                    with contextlib.suppress(discord.errors.NotFound):
                        await ctx.message.add_reaction("✅")

            ioloop.add_callback(react2)

    async def call_masswarn(
        self,
        ctx,
        level,
        members,
        unavailable_members,
        log_modlog,
        log_dm,
        take_action,
        reason=None,
        time=None,
        confirm: bool = False,
    ) -> None:  # sourcery no-metrics
        guild = ctx.guild
        message = None
        i = 0
        total_members = len(members)
        total_unavailable_members = len(unavailable_members)
        tick1 = "✅" if log_modlog else "❌"
        tick2 = "✅" if log_dm else "❌"
        tick3 = f"{'✅' if take_action else '❌'} Take action\n" if level != 1 else ""
        tick4 = f"{'✅' if time else '❌'} Time: " if level in [2, 5] else ""
        tick5 = "✅" if reason else "❌"
        time_str = (self.api._format_timedelta(time) + "\n") if time else ""

        async def update_count(count) -> None:
            nonlocal i
            i = count

        async def update_message() -> None:
            while True:
                nonlocal message
                content = f"Processing mass warning...\n{i}/{total_members + total_unavailable_members} {'members' if i != 1 else ('member')} warned ({round((i / total_members) * 100, 2)}%)\n\n{tick1} Log to the modlog\n{tick2} Send a DM to all members\n{tick3}{tick4} {time_str}\n{tick5} Reason: {reason or 'Not set'}"
                if message:
                    await message.edit(content=content)
                else:
                    message = await ctx.send(content)
                await asyncio.sleep(2)

        if unavailable_members and level < 5:
            await ctx.send(embed=make_e("You can only use `--hackban-select` with a level 5 warn.", status=3))
            return
        reason = await self.api.format_reason(ctx.guild, reason)
        if (log_modlog or log_dm) and reason and len(reason) > 2000:  # embed limits
            await ctx.send(
                embed=make_e(
                    "The reason is too long for an embed.\n\n*Tip: You can use Github Gist to write a long text formatted in Markdown, create a new file with the extension `.md` at the end and write as if you were on Discord.\n<https://gist.github.com/>*",
                    status=3,
                ),
            )
            return
        file = text_to_file("\n".join([f"{str(x)} ({x.id})" for x in members + unavailable_members]))
        targets = []
        if members:
            targets.append(
                f"{total_members} {'members' if total_members > 1 else ('member')} ({round((total_members / len(guild.members) * 100), 2)}% of the server)",
            )
        if unavailable_members:
            targets.append(f"{total_unavailable_members} {'users' if total_unavailable_members > 1 else ('user')} not in the server.")
        if not confirm:
            msg = await ctx.send(
                (
                    "You're about to set a level {level} warning on {target}.\n\n{tick1} Log to the modlog\n{tick2} Send a DM to all members\n{tick3}{tick4} {time}\n{tick5} Reason: {reason}\n\n{warning}Continue?"
                ).format(
                    level=level,
                    target=(" and ").join(targets),
                    tick1=tick1,
                    tick2=tick2,
                    tick3=tick3,
                    tick4=tick4,
                    time=time_str,
                    tick5=tick5,
                    reason=reason or ("Not set"),
                    warning=(
                        ":warning: You're about to warn a lot of members! Avoid doing this to prevent being rate limited by Discord, especially if you enabled DMs.\n\n"
                        if len(members) > 50 and level > 1
                        else ""
                    ),
                ),
                file=file,
            )
            menus.start_adding_reactions(msg, predicates.ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = predicates.ReactionPredicate.yes_or_no(msg, ctx.author)
            try:
                await self.bot.wait_for("reaction_add", check=pred, timeout=120)
            except AsyncTimeoutError:
                if ctx.guild.me.guild_permissions.manage_messages:
                    await msg.clear_reactions()
                else:
                    for reaction in msg.reactions():
                        await msg.remove_reaction(reaction, ctx.guild.me)
                return
            if not pred.result:
                await ctx.send("Mass warn cancelled.")
                return
            task = create_task(update_message())
        try:
            fails = await self.api.warn(
                guild=guild,
                members=members + unavailable_members,
                author=ctx.author,
                level=level,
                reason=reason,
                time=time,
                log_modlog=log_modlog,
                log_dm=log_dm,
                take_action=take_action,
                progress_tracker=None if confirm else update_count,
            )

        except errors.MissingPermissions as e:
            await ctx.send(e)
        except errors.LostPermissions as e:
            await ctx.send(e)
        except errors.MissingMuteRole:
            if not confirm:
                await ctx.send("You need to set up the mute role before doing this.\nUse the `;warnset mute` command for this.")
        except errors.NotFound:
            if not confirm:
                await ctx.send("Please set up a modlog channel before warning a member.\n\n*Use the `;warnset channel` command.*\n\n")
        else:
            if not confirm:
                if fails:
                    await ctx.send(
                        f"Done! However I couldn't take action against {len(fails)} {'members' if len(fails) > 1 else ('member')} out of {total_members}. ",
                    )
                else:
                    await ctx.send(f"Done! I've sucessfully taken action against {total_members} {'members' if total_members > 1 else ('member')}")
            else:

                @asyncretry(max_tries=3, pause=1)
                async def add_check():
                    with contextlib.suppress(discord.errors.NotFound):
                        await ctx.message.add_reaction("✅")

                await add_check()

        finally:
            if not confirm:
                task.cancel()
            if message:
                await message.delete()

    # all warning commands
    @commands.group(invoke_without_command=True, name="warn")
    @commands.has_guild_permissions(manage_messages=True)
    @commands.guild_only()
    async def _warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = None) -> None:
        """Take actions against a user and log it. The warned user will receive a
        DM.

        If not given, the warn level will be 1.

        """
        await self.call_warn(ctx, 1, member, reason)

    @_warn.command(hidden=True)
    @commands.has_guild_permissions(manage_messages=True)
    async def warn_1(self, ctx: commands.Context, member: discord.Member, *, reason: str = None) -> None:
        """Set a simple warning on a user.

        Note: You can either call `;warn 1` or `;warn`.

        """
        await self.call_warn(ctx, 1, member, reason)

    # @commands.command(usage="<member> [time] <reason>", aliases=["m"])
    # @commands.has_guild_permissions(manage_roles=True, manage_messages=True)
    #
    # @commands.has_guild_permissions(manage_roles=True, manage_messages=True)
    # async def mute(
    #     self, ctx: commands.Context, member: discord.Member, time: Optional[TimedeltaConverter], *, reason: str = None
    # ):
    #     """
    #     Mute the user in all channels, including voice channels.

    #     This mute will use a role that will automatically be created, if it was not already done.
    #     """

    @commands.cooldown(12, 5, commands.BucketType.guild)
    @commands.command(usage="<member> <reason>")
    @commands.has_guild_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """Kick the member from the server."""
        anti: AntiNuke = self.bot.get_cog("AntiNuke")

        try:
            await anti.track_kick(ctx.guild, ctx.author)
        except AntinukeLimitSurpassed:
            return await ctx.send(embed=make_e("Antinuke threshold met", 3))

        await self.call_warn(ctx, 3, member, reason)

    @commands.command(usage="<member>")
    @commands.has_guild_permissions(manage_roles=True)
    async def timedout(self, ctx: commands.Context):
        """List all Members from the server that are currently on timeout."""
        member_list = [
            [
                member.mention,
                member.id,
                member.communication_disabled_until,
                humanize.naturaldelta(arrow.now() - arrow.get(member.communication_disabled_until), "seconds"),
            ]
            for member in ctx.guild.members
            if member.communication_disabled_until
        ]

        member_list.sort(key=lambda member: member[2], reverse=True)
        member_string = "".join(f"\n{member[0]} ({member[1]}) {member[3]}" for member in member_list)

        pages = []
        for page in pagify(escape(member_string, formatting=True), page_length=1000):
            embed = discord.Embed(description=page, color=3092790)
            embed.set_author(name=f"{ctx.guild.name}'s timed out members", icon_url=ctx.guild.icon_url_as(format="png"))
            pages.append(embed)

        for page_counter, page in enumerate(pages, start=1):
            page.set_footer(text=f"Page {page_counter} out of {len(pages)}")
        if not pages:
            return await ctx.send("Nobody is timed out")
        await menu(ctx, pages=pages, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=90)

    @commands.command(usage="<member>", aliases=["ut", "unmute", "um"])
    @commands.max_concurrency(1, commands.BucketType.guild)
    @commands.has_guild_permissions(manage_roles=True)
    async def untimeout(self, ctx: commands.Context, *, member: str):
        """Remove a member's timeout."""
        guild: discord.Guild = ctx.guild

        if member == "all":
            if ctx.author.id not in self.bot.owner_ids and not ctx.author.guild_permissions.administrator:
                return await ctx.send(embed=make_e("You need to be an administrator to untimeout everyone", 3))
            try:
                count = 0
                timed_out_members = [m for m in guild.members if m.communication_disabled_until]
                if not timed_out_members:
                    return await ctx.send(embed=make_e("There's nobody to remove the timeout from", 2))

                async with ctx.typing():
                    tracker = await ctx.send(embed=make_e(f"Removed timeout for {count}/{len(timed_out_members)}", "info"))
                    for m in timed_out_members:
                        async with asyncio.timeout(60):
                            with log.catch(exclude=asyncio.CancelledError):
                                await set_timeout(ctx, ctx.guild.id, m.id, None)
                        count += 1
                        if not await self.bot.redis.ratelimited(f"ut:{ctx.guild.id}", 1, 1):
                            await tracker.edit(embed=make_e(f"Removed timeout for {count}/{len(timed_out_members)}", "info"))
            except discord.HTTPException as e:
                await ctx.send(embed=make_e(f"Erorr from discord: {e}"))
                await tracker.delete()
            else:
                await tracker.edit(embed=make_e("Reset all member's timeout"))

        else:
            async with ctx.typing():
                if isinstance(member, str):
                    conv = MemberConverter()
                    try:
                        member = await conv.convert(ctx, member)
                    except MemberNotFound:
                        return await ctx.send(embed=make_e(f"Server member **{member}** not found", 3))
                if ctx.author.id not in self.bot.owner_ids and ctx.author.top_role <= member.top_role:
                    return await ctx.send(embed=make_e("You can only remove a timeout of a user whos top role is below yours."))

                try:
                    if not member.communication_disabled_until:
                        return await ctx.send(embed=make_e(f"{member.mention} is not currently on timeout", status=2))
                    await set_timeout(ctx, ctx.guild.id, member.id, None)
                    return await ctx.send(embed=make_e(f"Timeout removed for {member.mention}"))
                except DiscordTimeoutError as e:
                    return await ctx.send(embed=make_e(str(e), status=3))

    @commands.command(usage="<member> [time] ", aliases=["t", "m", "mute"])
    @commands.has_guild_permissions(manage_roles=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, *, duration: str = "5m"):
        """Set a members timeout. Supports weeks, days, hours, minutes, and
        seconds, with or without decimal points.

        Examples: "8h", "10 seconds",  "2 weeks 1 day", "1d 2h 3.5m 0s"

        """
        if ctx.author.id not in self.bot.owner_ids and ctx.author.top_role <= member.top_role:
            return await ctx.send(embed=make_e("You can only set a timeout of a user whos top role is below yours."))

        try:
            if duration == "none":
                raise ValueError
            dt = parse_timedelta(duration)
            target_date = arrow.now() + dt
            max_age = arrow.now().shift(days=28)
            if target_date > max_age:
                return await ctx.send(embed=make_e("Timeouts must be less than 28 days", status=2))
        except ValueError:
            return await ctx.send(embed=make_e("Invalid time unit. Expected a time unit such as w, d, s", status=2, tip=";timeout @member 10m"))

        try:
            async with ctx.typing():
                await set_timeout(ctx, ctx.guild.id, member.id, target_date)
        except DiscordTimeoutError as e:
            return await ctx.send(embed=make_e(str(e), status=3))

        return await ctx.send(embed=make_e(f"{member.mention} timeout set for **{precisedelta(dt)}**"))

    @commands.command(usage="<member> [time] <reason>")
    @commands.has_guild_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        r"""Softban the member from the server.

        This means that the user will be banned and immediately
        unbanned, so it will purge their\ messages in all channels.

        """
        anti: AntiNuke = self.bot.get_cog("AntiNuke")

        try:
            await anti.track_ban(ctx.guild, ctx.author)
        except AntinukeLimitSurpassed:
            return await ctx.send(embed=make_e("Antinuke threshold met", 3))
        await self.call_warn(ctx, 4, member, reason)

    @commands.command(usage="<member> [time] <reason>")
    @commands.has_guild_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: Union[discord.Member, UnavailableMember], time: Optional[TimedeltaConverter], *, reason: str = None):
        """Ban the member from the server.

        This ban can be a normal ban, a temporary ban or a hack ban
        (bans a user not in the server).

        """
        anti: AntiNuke = self.bot.get_cog("AntiNuke")

        try:
            await anti.track_ban(ctx.guild, ctx.author)
        except AntinukeLimitSurpassed:
            return await ctx.send(embed=make_e("Antinuke threshold met", 3))

        await self.call_warn(ctx, 5, member, reason, time)

    @checks.is_owner()
    @commands.group(invoke_without_command=True, hidden=True)
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def masswarn(self, ctx, *selection: str) -> None:
        r"""Perform a warn on multiple members at once.

        To select members, you have to use UNIX-like flags to add
        conditions\ which will be checked for each member.

        """
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            1,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            None,
            selection.confirm,
        )

    @masswarn.command(name="1")
    @commands.has_guild_permissions(administrator=True)
    async def masswarn_1(self, ctx, *selection: str) -> None:
        """Perform a simple mass warning."""
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            1,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            None,
            selection.confirm,
        )

    @masswarn.command(name="2")
    @commands.has_guild_permissions(administrator=True)
    async def masswarn_2(self, ctx, *selection: str) -> None:
        r"""Perform a mass mute.

        You can provide a duration with the `--time` flag, the format is
        the same as the simple\ level 2 warning.

        """
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            2,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            selection.time,
            selection.confirm,
        )

    @masswarn.command(name="3")
    @commands.has_guild_permissions(administrator=True)
    async def masswarn_3(self, ctx, *selection: str) -> None:
        """Perform a mass kick."""
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            3,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            None,
            selection.confirm,
        )

    @masswarn.command(name="4")
    @commands.has_guild_permissions(administrator=True)
    async def masswarn_4(self, ctx, *selection: str) -> None:
        """Perform a mass softban."""
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            4,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            None,
            selection.confirm,
        )

    @masswarn.command(name="5")
    @commands.has_guild_permissions(administrator=True)
    async def masswarn_5(self, ctx, *selection: str) -> None:
        r"""Perform a mass ban.

        You can provide a duration with the `--time` flag, the format is
        the same as the simple\ level 5 warning.

        """
        if not selection:
            await ctx.send_help()
            return
        try:
            selection = await AdvancedMemberSelect().convert(ctx, selection)
        except commands.BadArgument as e:
            await ctx.send(e)
            return
        await self.call_masswarn(
            ctx,
            5,
            selection.members,
            selection.unavailable_members,
            selection.send_modlog,
            selection.send_dm,
            selection.take_action,
            selection.reason,
            selection.time,
            selection.confirm,
        )

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.member)
    async def warnings(self, ctx: commands.Context, user: UnavailableMember = None, index: int = 0) -> None:
        # sourcery no-metrics skip: dict-assign-update-to-union
        """Shows all warnings of a member.

        This command can be used by everyone, but only moderators can
        see other's warnings. Moderators can also edit or delete
        warnings by using the reactions.

        """
        if not user:
            user = ctx.author
        if not await mod.is_mod_or_superior(self.bot, ctx.author) and not ctx.author.guild_permissions.kick_members and user.id != ctx.author.id:
            await ctx.send("You are not allowed to see other's warnings!")
            return
        cases = await self.api.get_all_cases(ctx.guild, user)
        if not cases:
            await ctx.send("That member was never warned.")
            return
        if 0 < index < len(cases):
            await ctx.send("That case doesn't exist.")
            return

        def total(level):
            return len([x for x in cases if x["level"] == level])

        def warning_str(level, plural):
            return {1: ("Warning", "Warnings"), 2: ("Mute", "Mutes"), 3: ("Kick", "Kicks"), 4: ("Softban", "Softbans"), 5: ("Ban", "Bans")}.get(
                level,
                "unknown",
            )[1 if plural else 0]

        msg = []
        for i in range(6):
            total_warns = total(i)
            if total_warns > 0:
                msg.append(f"{warning_str(i, total_warns > 1)}: {total_warns}")
        warn_field = "\n".join(msg) if len(msg) > 1 else msg[0]
        warn_list = []
        for case in cases[:-10:-1]:
            level = case["level"]
            reason = str(case["reason"]).splitlines()
            reason = f"{reason[0]}..." if len(reason) > 1 else reason[0]

            log.info(case)

            date = _time.time() - int(case["time"])

            date_str = humanize.naturaltime(date)
            text = f"**{warning_str(level, False)}:** {reason} • **{date_str}**\n"
            if len("".join([*warn_list, text])) > 1024:  # embed limits
                break
            else:
                warn_list.append(text)
        cache_system = self.bot.get_cog("ExecutionsTracker")
        if not user:
            user = await cache_system.cache_query(user.id, ctx.guild)
        embed = discord.Embed(description="User modlog summary.")
        embed.set_author(name=f"{user} | {user.id}")
        embed.add_field(name=f"Total number of warnings: {len(cases)}", value=warn_field, inline=False)
        embed.add_field(name=f"{len(warn_list)} last warnings" if len(warn_list) > 1 else ("Last warning"), value="".join(warn_list), inline=False)
        embed.set_footer(text="Click on the reactions to scroll through the warnings")
        embeds = [embed]
        for i, case in enumerate(cases):
            level = case["level"]
            mod_id = int(case["author"])
            moderator = ctx.guild.get_member(mod_id)
            if moderator:
                mod_msg = moderator.mention
            if not moderator:
                modquery = await cache_system.cache_query(mod_id, ctx.guild)
                mod_msg = str(modquery)
            if mod_msg == "Unknown#1000":
                mod_msg = f"ID: {mod_id}"
            moderator = mod_msg
            time = self.api._get_datetime(case["time"])
            embed = discord.Embed(description=f"Case #{i + 1} informations")

            embed.set_author(name=f"{user} | {user.id}")
            embed.add_field(name="Level", value=f"{warning_str(level, False)} ({level})", inline=True)
            embed.add_field(name="Moderator", value=moderator, inline=True)
            if case["duration"]:
                duration = self.api._get_timedelta(case["duration"])
                embed.add_field(name="Duration", value=f"{self.api._format_timedelta(duration)}\n(Until {self.api._format_datetime(time + duration)})")
            embed.add_field(name="Reason", value=case["reason"], inline=False),
            embed.timestamp = time
            embed.colour = await self.data.guild(ctx.guild).colors.get_raw(level)
            embeds.append(embed)

        controls = {"⬅": menus.prev_page, "❌": menus.close_menu, "➡": menus.next_page}
        if await mod.is_mod_or_superior(self.bot, ctx.author) and ctx.author.id != user.id:
            controls |= {"✏": self._edit_case, "🗑": self._delete_case}

        await menus.menu(ctx=ctx, pages=embeds, controls=controls, message=None, page=index, timeout=60)

    async def _edit_case(
        self,
        ctx: commands.Context,
        pages: list,
        controls: dict,
        message: discord.Message,
        page: int,
        timeout: float,
        emoji: str,
    ):  # sourcery no-metrics
        """Edit a case, this is linked to the warnings menu system."""

        async def edit_message(channel_id: int, message_id: int, new_reason: str) -> bool:
            channel: discord.TextChannel = guild.get_channel(channel_id)
            if channel is None:
                log.warning(f"[Guild {guild.id}] Failed to edit modlog message. Channel {channel_id} not found.")
                return False
            try:
                message: discord.Message = await channel.fetch_message(message_id)
            except discord.errors.NotFound:
                log.warning(f"[Guild {guild.id}] Failed to edit modlog message. Message {message_id} in channel {channel.id} not found.")
                return False
            except discord.errors.Forbidden:
                log.warning(f"[Guild {guild.id}] Failed to edit modlog message. No permissions to fetch messages in channel {channel.id}.")
                return False
            except discord.errors.HTTPException:
                log.exception(f"[Guild {guild.id}] Failed to edit modlog message. API exception raised.")
                return False
            try:
                embed: discord.Embed = message.embeds[0]
                embed.set_field_at(len(embed.fields) - 2, name="Reason", value=new_reason, inline=False)
            except IndexError:
                log.exception(f"[Guild {guild.id}] Failed to edit modlog message. Embed is malformed.")
                return False
            try:
                await message.edit(embed=embed)
            except discord.errors.HTTPException:
                log.exception(f"[Guild {guild.id}] Failed to edit modlog message. Unknown error when attempting message edition.")
                return False
            return True

        guild = ctx.guild
        if page == 0:
            # first page, no case to edit
            await message.remove_reaction(emoji, ctx.author)
            return await menus.menu(ctx, pages, controls, message=message, page=page, timeout=timeout)
        await message.clear_reactions()
        try:
            old_embed = message.embeds[0]
        except IndexError:
            return
        embed = discord.Embed()
        member_id = int(re.match(r"(?:.*#[0-9]{4})(?: \| )([0-9]{15,21})", old_embed.author.name).group(1))
        member = await self.bot.fetch_user(member_id)
        embed.clear_fields()
        embed.description = f"Case #{page} edition.\n\n**Please type the new reason to set**"
        embed.set_footer(text="You have two minutes to type your text in the chat.")
        case = (await self.data.custom("MODLOGS", guild.id, member.id).x())[page - 1]
        await message.edit(embed=embed)
        try:
            response = await self.bot.wait_for("message", check=predicates.MessagePredicate.same_context(ctx), timeout=120)
        except AsyncTimeoutError:
            await message.delete()
            return
        case = (await self.data.custom("MODLOGS", guild.id, member.id).x())[page - 1]
        new_reason = await self.api.format_reason(guild, response.content)
        embed.description = f"Case #{page} edition."
        embed.add_field(name="Old reason", value=case["reason"], inline=False)
        embed.add_field(name="New reason", value=new_reason, inline=False)
        embed.set_footer(text="Click on ✅ to confirm the changes.")
        await message.edit(embed=embed)
        menus.start_adding_reactions(message, predicates.ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = predicates.ReactionPredicate.yes_or_no(message, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred, timeout=30)
        except AsyncTimeoutError:
            await message.clear_reactions()
            await message.edit(content="Question timed out.", embed=None)
            return
        if pred.result:
            async with self.data.custom("MODLOGS", guild.id, member.id).x() as logs:
                logs[page - 1]["reason"] = new_reason
                try:
                    (channel_id, message_id) = logs[page - 1]["modlog_message"].values()
                except KeyError:
                    result = None
                else:
                    result = await edit_message(channel_id, message_id, new_reason)
            await message.clear_reactions()
            text = "The reason was successfully edited!\n"
            if result is False:
                text += "*The modlog message couldn't be edited..*"
            await message.edit(content=text, embed=None)
        else:
            await message.clear_reactions()
            await message.edit(content="The reason was not edited.", embed=None)

    async def _delete_case(
        self,
        ctx: commands.Context,
        pages: list,
        controls: dict,
        message: discord.Message,
        page: int,
        timeout: float,
        emoji: str,
    ) -> None:  # sourcery no-metrics
        """Remove a case, this is linked to the warning system."""

        async def delete_message(channel_id: int, message_id: int) -> bool:
            channel: discord.TextChannel = guild.get_channel(channel_id)
            if channel is None:
                log.warning(f"[Guild {guild.id}] Failed to delete modlog message. Channel {channel_id} not found.")
                return False
            try:
                message: discord.Message = await channel.fetch_message(message_id)
            except discord.errors.NotFound:
                log.warning(f"[Guild {guild.id}] Failed to delete modlog message. Message {message_id} in channel {channel.id} not found.")
                return False
            except discord.errors.Forbidden:
                log.warning(f"[Guild {guild.id}] Failed to delete modlog message. No permissions to fetch messages in channel {channel.id}.")
                return False
            except discord.errors.HTTPException:
                log.exception(f"[Guild {guild.id}] Failed to delete modlog message. API exception raised.")
                return False
            try:
                await message.delete()
            except discord.errors.HTTPException:
                log.exception(f"[Guild {guild.id}] Failed to delete modlog message. Unknown error when attempting message deletion.")
                return False
            return True

        guild = ctx.guild
        await message.clear_reactions()
        try:
            old_embed = message.embeds[0]
        except IndexError:
            return
        embed = discord.Embed()
        member_id = int(re.match(r"(?:.*#[0-9]{4})(?: \| )([0-9]{15,21})", old_embed.author.name).group(1))
        member = await self.bot.fetch_user(member_id)
        if page == 0:
            # no warning specified, mod wants to completly clear the member
            embed.colour = 0xEE2B2B
            embed.description = f"Member {str(member)}'s clearance. By selecting ❌ on the user modlog summary, you can remove all warnings given to {str(member)}. __All levels and notes are affected.__\n**Click on the reaction to confirm the removal of the entire user's modlog. This cannot be undone.**"
        else:
            level = int(re.match(r".*\(([0-9]*)\)", old_embed.fields[0].value).group(1))
            can_unmute = False
            add_roles = False
            if level == 2:
                mute_role = guild.get_role(await self.cache.get_mute_role(guild))
                member = guild.get_member(member.id)
                if member:
                    if mute_role and mute_role in member.roles:
                        can_unmute = True
                    add_roles = await self.data.guild(guild).remove_roles()
            description = f"Case #{page} deletion.\n**Click on the reaction to confirm your action.**"
            if can_unmute or add_roles:
                description += "\nNote: Deleting the case will also do the following:"
                if can_unmute:
                    description += "\n- unmute the member"
                if add_roles:
                    description += "\n- add all roles back to the member"
            embed.description = description
        await message.edit(embed=embed)
        menus.start_adding_reactions(message, predicates.ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = predicates.ReactionPredicate.yes_or_no(message, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred, timeout=30)
        except AsyncTimeoutError:
            await message.clear_reactions()
            await message.edit(content="Question timed out.", embed=None)
            return
        if not pred.result:
            await message.clear_reactions()
            await message.edit(content="Nothing was removed.", embed=None)
            return
        if page == 0:
            # removing entire modlog
            await self.data.custom("MODLOGS", guild.id, member.id).x.set([])
            log.info(f"[Guild {guild.id}] Cleared modlog of member {member} (ID: {member.id}).")
            await message.clear_reactions()
            await message.edit(content="User modlog cleared.", embed=None)
            return
        async with self.data.custom("MODLOGS", guild.id, member.id).x() as logs:
            try:
                roles = logs[page - 1]["roles"]
            except KeyError:
                roles = []
            try:
                (channel_id, message_id) = logs[page - 1]["modlog_message"].values()
            except KeyError:
                result = None
            else:
                result = await delete_message(channel_id, message_id)
            logs.remove(logs[page - 1])
        log.info(f"[Guild {guild.id}] Removed case #{page} from member {member} (ID: {member.id}).")
        await message.clear_reactions()
        if can_unmute:
            await member.remove_roles(mute_role, reason=("Warning deleted by {author}").format(author=f"{str(ctx.author)} (ID: {ctx.author.id})"))
        if roles:
            roles = [guild.get_role(x) for x in roles]
            await member.add_roles(*roles, reason="Adding removed roles back after unmute.")
        text = "The case was successfully deleted!"
        if result is False:
            text += "*The modlog message couldn't be deleted. Check your logs for details.*"
        await message.edit(content="The case was successfully deleted!", embed=None)

    def clean_bans(self, guild: discord.Guild) -> None:
        """Cleans the bans list of a guild."""

    @commands.command()
    @checks.has_permissions(kick_members=True)
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def warnlist(self, ctx: commands.Context) -> None:
        """List the latest warnings issued on the server.

        Will only generate up to 150 cases.

        """
        async with ctx.typing():
            guild = ctx.guild
            full_text = ""
            warns = await self.api.get_all_cases(guild, limit=200)
        if not warns:
            await ctx.send("No warnings have been issued in this server yet.")
            return
        for i, warn in enumerate(warns, start=1):
            text = (
                "--- Case {number} ---\nMember:    {member} (ID: {member.id})\nLevel:     {level}\nReason:    {reason}\nAuthor:    {author} (ID: {author.id})\nDate:      {time}\n"
            ).format(number=i, **warn)
            if warn["duration"]:
                duration = self.api._get_timedelta(warn["duration"])
                text += f"Duration:  {self.api._format_timedelta(duration)}\nUntil:     {self.api._format_datetime(warn['time'] + duration)}\n"
            text += "\n\n"
            full_text = text + full_text
        pages = list(pagify(full_text, delims=["\n\n", "\n"], priority=True, page_length=800))

        total_pages = len(pages)
        total_warns = len(warns)
        pages = [f"```yml\n{x}```\n" + f"{total_warns} warnings. Page {i}/{total_pages}" for i, x in enumerate(pages, start=1)]
        await menus.menu(ctx=ctx, pages=pages, controls=menus.DEFAULT_CONTROLS, timeout=220)

    @commands.command()
    @commands.guild_only()
    async def reason(self, ctx: commands.Context, *, reason: str):
        """Specify a reason for a modlog case.

        Please note that you can only edit cases you are the owner of
        unless you are a mod, admin or server owner.

        If no case number is specified, the latest case will be used.

        """
        author: discord.Member = ctx.author
        guild: discord.Guild = ctx.guild

        user_id = await self.api.get_latest_case(guild.id)
        async with self.data.custom("MODLOGS", ctx.guild.id, user_id).x() as logs:
            if not logs:
                return await ctx.send(embed=make_e("No case found", 2))

            case = logs[-1]

            if guild.owner != author and case["author"] != author.id:
                return await ctx.send(embed=make_e("You can only edit cases that you create", 2))

            case["reason"] = reason

        await ctx.tick()

    # @commands.command(aliases=["wsunmute", "um", "umute"])
    # @checks.has_permissions(manage_roles=True)
    # async def unmute(self, ctx: commands.Context, member: discord.Member):
    #     """
    #     Unmute a member.

    #     If the member's roles were removed, they will be granted back.
    #     """
    #     if not mute_role:
    #     if mute_role not in member.roles:
    #     if case and case["level"] == 2:
    #         for data in cases[::-1]:
    #             if data["level"] == 2:
    #     await member.remove_roles(
    #     if not roles:
    #     async with ctx.typing():
    #         for role in roles:
    #                 log.exception(
    #     if fails:
    #         for role in fails:
    #     for page in pagify(text):

    @listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        # if a member gets unbanned, we check if he was temp banned with warnsystem
        # if it was, we remove the case so it won't unban him a second time
        warns = await self.cache.get_temp_action(guild)
        if to_remove := [UnavailableMember(self.bot, guild._state, member) for member, data in warns.items() if data["level"] != 2 and int(member) == user.id]:
            await self.cache.bulk_remove_temp_action(guild, to_remove)
            log.info(f"[Guild {guild.id}] The temporary ban of user {user} (ID: {user.id}) was cancelled due to his manual unban.")

    @listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.bot.is_ready():
            return
        guild = after.guild
        mute_role = guild.get_role(await self.cache.get_mute_role(guild))
        if not mute_role:
            return
        if mute_role not in before.roles or mute_role in after.roles:
            return

        if after.id in self.cache.temp_actions:
            await self.cache.remove_temp_action(guild, after)

    @listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        guild = channel.guild
        if isinstance(channel, discord.VoiceChannel):
            return
        if not await self.data.guild(guild).update_mute():
            return
        role = guild.get_role(await self.cache.get_mute_role(guild))
        if not role:
            return
        try:
            await channel.set_permissions(
                role,
                send_messages=False,
                add_reactions=False,
                reason="Updating channel settings so the mute role will work here. Disable the auto-update with ;warnset autoupdate",
            )
        except discord.errors.Forbidden:
            log.warning(f"[Guild {guild.id}] Couldn't update permissions of new channel {channel.name} (ID: {channel.id}) due to a permission error.")
        except discord.errors.HTTPException:
            log.exception(f"[Guild {guild.id}] Couldn't update permissions of new channel {channel.name} (ID: {channel.id}) due to an unknown error.")

    @listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member) -> None:
        if not guild.me.guild_permissions.view_audit_log:
            return
        # check for that before doing anything else, means WarnSystem isn't setup

        await asyncio.sleep(0.1)

        if await check_ban_key(member):
            return

        with contextlib.suppress(errors.NotFound):
            await self.api.get_modlog_channel(guild, 5)
        when = datetime.utcnow()
        before = when + timedelta(minutes=1)
        after = when - timedelta(minutes=1)
        attempts = 0
        # wait up to an hour to find a matching case
        while attempts < 12:
            attempts += 1
            try:
                entry = await guild.audit_logs(action=discord.AuditLogAction.ban, before=before, after=after).find(
                    lambda e: e.target.id == member.id and after < e.created_at < before,
                )
            except discord.Forbidden:
                break
            except discord.HTTPException:
                pass
            else:
                if entry:
                    if entry.user.id != guild.me.id:
                        # Don't create modlog entires for the bot's own bans, cogs do this.
                        (mod, reason, date) = (entry.user, entry.reason, entry.created_at)
                        if isinstance(member, discord.User):
                            member = UnavailableMember(self.bot, guild._state, member.id)
                        try:
                            await self.api.warn(guild, [member], mod, 5, reason, date=date, log_dm=False, take_action=False, log_modlog=False)
                        except Exception:
                            log.exception(
                                f"[Guild {guild.id}] Failed to create a case based on manual ban. Member: {member} ({member.id}). Author: {mod} ({mod.id}). Reason: {reason}",
                            )
                    return
            await asyncio.sleep(300)

    # correctly unload the cog
    def __unload(self) -> None:
        self.cog_unload()

    def cog_unload(self) -> None:
        log.info("Unloading cog...")

        # remove all handlers from the logger, this prevents adding
        # multiple times the same handler if the cog gets reloaded

        # stop checking for unmute and unban
        self.task.cancel()
        self.api.disable_automod()
