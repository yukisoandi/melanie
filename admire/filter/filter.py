from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import timezone
from typing import Union

import discord
import discord.client
import regex as re
from melaniebot.core import Config, checks, commands, modlog
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list, pagify
from melaniebot.core.utils.predicates import MessagePredicate

from melanie import BaseModel, yesno
from melanie.helpers import make_e


def _(x):
    return x


class GuildSettings(BaseModel):
    filter = []
    filterban_count = 0
    filterban_time = 0
    filter_names = False
    filter_default_name = "John Doe"
    filter_statuses = False
    status_remove_roles = []


class Filter(commands.Cog):
    """This cog is designed for "filtering" unwanted words and phrases from a
    server.

    It provides tools to manage a list of words or sentences, and to
    customize automatic actions to be taken against users who use those
    words in channels or in their name/nickname.

    This can be used to prevent inappropriate language, off-topic
    discussions, invite links, and more.

    """

    def __init__(self, bot: Melanie) -> None:
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, 4766951341)

        default_member_settings = {"filter_count": 0, "next_reset_time": 0}
        default_channel_settings = {"filter": []}
        self.config.register_guild(**GuildSettings().dict())
        self.config.register_member(**default_member_settings)
        self.config.register_channel(**default_channel_settings)
        self.pattern_cache = {}
        self.guild_cache = {}
        self.locks = defaultdict(asyncio.Lock)

    async def initialize(self) -> None:
        await self.register_casetypes()

    @staticmethod
    async def register_casetypes() -> None:
        await modlog.register_casetypes(
            [
                {"name": "filterban", "default_setting": False, "image": "\N{FILE CABINET}\N{VARIATION SELECTOR-16} \N{HAMMER}", "case_str": "Filter ban"},
                {"name": "filterhit", "default_setting": False, "image": "\N{FILE CABINET}\N{VARIATION SELECTOR-16}", "case_str": "Filter hit"},
            ],
        )

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def filterset(self, ctx: commands.Context):
        """Base command to manage filter settings."""

    @filterset.command(name="defaultname")
    async def filter_default_name(self, ctx: commands.Context, name: str):
        """Set the nickname for users with a filtered name.

        Note that this has no effect if filtering names is disabled
        (to toggle, run `;filter names`).

        The default name used is *John Doe*.

        Example:
        -------
            - `;filterset defaultname Missingno`

        **Arguments:**

        - `<name>` The new nickname to assign.

        """
        guild = ctx.guild
        await self.config.guild(guild).filter_default_name.set(name)
        await ctx.send("The name to use on filtered names has been set.")

    @filterset.command(hidden=True, name="ban")
    async def filter_ban(self, ctx: commands.Context, count: int, timeframe: int):
        """Set the filter's autoban conditions.

        Users will be banned if they send `<count>` filtered words in
        `<timeframe>` seconds.

        Set both to zero to disable autoban.

        Examples
        --------
            - `;filterset ban 5 5` - Ban users who say 5 filtered words in 5 seconds.
            - `;filterset ban 2 20` - Ban users who say 2 filtered words in 20 seconds.

        **Arguments:**

        - `<count>` The amount of filtered words required to trigger a ban.
        - `<timeframe>` The period of time in which too many filtered words will trigger a ban.

        """
        if (count <= 0) != (timeframe <= 0):
            await ctx.send("Count and timeframe either both need to be 0 or both need to be greater than 0!")
            return
        elif count == 0 and timeframe == 0:
            async with self.config.guild(ctx.guild).all() as guild_data:
                guild_data["filterban_count"] = 0
                guild_data["filterban_time"] = 0
            await ctx.send("Autoban disabled.")
        else:
            async with self.config.guild(ctx.guild).all() as guild_data:
                guild_data["filterban_count"] = count
                guild_data["filterban_time"] = timeframe
            await ctx.send("Count and time have been set.")

    @commands.group(name="filter")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def _filter(self, ctx: commands.Context):
        """Base command to add or remove words from the server filter.

        Use double quotes to add or remove sentences.

        """

    @_filter.command(name="status")
    async def _filter_status(self, ctx: commands.Context):
        """Toggle whether filter should remove configured roles from a user if
        their status contains a filtered word.
        """
        state = await self.config.guild(ctx.guild).filter_statuses()
        if state:
            confirmed, _msg = await yesno("Status monitoring is currently enabled. Would you like to disable?")
            if confirmed:
                await self.config.guild(ctx.guild).filter_statuses.set(False)

        else:
            confirmed, _msg = await yesno("Status monitoring is currently disabled. Would you like to enable?")
            if confirmed:
                await self.config.guild(ctx.guild).filter_statuses.set(True)

    @_filter.command(name="hoistrole")
    async def _filter_hoistrole(self, ctx: commands.Context, role: discord.Role):
        """Configure the roles to be removed from a user if their status contains
        a filtered word.
        """
        async with self.config.guild(ctx.guild).all() as _settings:
            settings = GuildSettings(**_settings)

            if role.id in settings.status_remove_roles:
                settings.status_remove_roles.remove(role.id)
                await ctx.send(embed=make_e(f"Removed {role.name} from filter status roles"))
            else:
                settings.status_remove_roles.append(role.id)
                settings.filter_statuses = True
                await ctx.send(embed=make_e(f"Added {role.name} to filter status roles"))
            _settings.update(settings.dict())

    @_filter.command(name="clear")
    async def _filter_clear(self, ctx: commands.Context):
        """Clears this server's filter list."""
        guild = ctx.guild
        author = ctx.author
        filter_list = await self.config.guild(guild).filter()
        if not filter_list:
            return await ctx.send("The filter list for this server is empty.")
        await ctx.send("Are you sure you want to clear this server's filter list?" + " (yes/no)")
        try:
            pred = MessagePredicate.yes_or_no(ctx, user=author)
            await ctx.bot.wait_for("message", check=pred, timeout=60)
        except TimeoutError:
            await ctx.send("You took too long to respond.")
            return
        if pred.result:
            await self.config.guild(guild).filter.clear()
            await self.invalidate_cache(guild)
            await ctx.send("Server filter cleared.")
        else:
            await ctx.send("No changes have been made.")

    @_filter.command(name="list")
    async def _global_list(self, ctx: commands.Context):
        """Send a list of this server's filtered words."""
        server = ctx.guild
        author = ctx.author
        word_list = await self.config.guild(server).filter()
        if not word_list:
            await ctx.send("There are no current words setup to be filtered in this server.")
            return
        words = humanize_list(word_list)
        words = "Filtered in this server:" + "\n\n" + words
        try:
            for page in pagify(words, delims=[" ", "\n"], shorten_by=8):
                await author.send(page)
        except discord.Forbidden:
            await ctx.send("I can't send direct messages to you.")

    @_filter.group(name="channel")
    async def _filter_channel(self, ctx: commands.Context):
        """Base command to add or remove words from the channel filter.

        Use double quotes to add or remove sentences.

        """

    @_filter_channel.command(name="clear")
    async def _channel_clear(self, ctx: commands.Context):
        """Clears this channel's filter list."""
        channel = ctx.channel
        author = ctx.author
        filter_list = await self.config.channel(channel).filter()
        if not filter_list:
            return await ctx.send("The filter list for this channel is empty.")
        await ctx.send("Are you sure you want to clear this channel's filter list?" + " (yes/no)")
        try:
            pred = MessagePredicate.yes_or_no(ctx, user=author)
            await ctx.bot.wait_for("message", check=pred, timeout=60)
        except TimeoutError:
            await ctx.send("You took too long to respond.")
            return
        if pred.result:
            await self.config.channel(channel).filter.clear()
            await self.invalidate_cache(ctx.guild, channel)
            await ctx.send("Channel filter cleared.")
        else:
            await ctx.send("No changes have been made.")

    @_filter_channel.command(name="list")
    async def _channel_list(self, ctx: commands.Context):
        """Send a list of the channel's filtered words."""
        channel = ctx.channel
        author = ctx.author
        word_list = await self.config.channel(channel).filter()
        if not word_list:
            await ctx.send("There is no current words setup to be filtered in this channel.")
            return
        words = humanize_list(word_list)
        words = "Filtered in this channel:" + "\n\n" + words
        try:
            for page in pagify(words, delims=[" ", "\n"], shorten_by=8):
                await author.send(page)
        except discord.Forbidden:
            await ctx.send("I can't send direct messages to you.")

    @_filter_channel.command(name="add", require_var_positional=True)
    async def filter_channel_add(self, ctx: commands.Context, *words: str):
        """Add words to the filter.

        Use double quotes to add sentences.

        Examples
        --------
            - `;filter channel add word1 word2 word3`
            - `;filter channel add "This is a sentence"`

        **Arguments:**

        - `[words...]` The words or sentences to filter.

        """
        channel = ctx.channel
        added = await self.add_to_filter(channel, words)
        if added:
            await self.invalidate_cache(ctx.guild, ctx.channel)
            await ctx.send("Words added to filter.")
        else:
            await ctx.send("Words already in the filter.")

    @_filter_channel.command(name="delete", aliases=["remove", "del"], require_var_positional=True)
    async def filter_channel_remove(self, ctx: commands.Context, *words: str):
        """Remove words from the filter.

        Use double quotes to remove sentences.

        Examples
        --------
            - `;filter channel remove word1 word2 word3`
            - `;filter channel remove "This is a sentence"`

        **Arguments:**

        - `[words...]` The words or sentences to no longer filter.

        """
        channel = ctx.channel
        removed = await self.remove_from_filter(channel, words)
        if removed:
            await ctx.send("Words removed from filter.")
            await self.invalidate_cache(ctx.guild, ctx.channel)
        else:
            await ctx.send("Those words weren't in the filter.")

    @_filter.command(name="add", require_var_positional=True)
    async def filter_add(self, ctx: commands.Context, *words: str):
        """Add words to the filter.

        Use double quotes to add sentences.

        Examples
        --------
            - `;filter add word1 word2 word3`
            - `;filter add "This is a sentence"`

        **Arguments:**

        - `[words...]` The words or sentences to filter.

        """
        server = ctx.guild
        added = await self.add_to_filter(server, words)
        if added:
            await self.invalidate_cache(ctx.guild)
            await ctx.send("Words successfully added to filter.")
        else:
            await ctx.send("Those words were already in the filter.")

    @_filter.command(name="delete", aliases=["remove", "del"], require_var_positional=True)
    async def filter_remove(self, ctx: commands.Context, *words: str):
        """Remove words from the filter.

        Use double quotes to remove sentences.

        Examples
        --------
            - `;filter remove word1 word2 word3`
            - `;filter remove "This is a sentence"`

        **Arguments:**

        - `[words...]` The words or sentences to no longer filter.

        """
        server = ctx.guild
        removed = await self.remove_from_filter(server, words)
        if removed:
            await self.invalidate_cache(ctx.guild)
            await ctx.send("Words successfully removed from filter.")
        else:
            await ctx.send("Those words weren't in the filter.")

    @_filter.command(name="names")
    async def filter_names(self, ctx: commands.Context):
        """Toggle name and nickname filtering.

        This is disabled by default.

        """
        guild = ctx.guild

        async with self.config.guild(guild).all() as guild_data:
            current_setting = guild_data["filter_names"]
            guild_data["filter_names"] = not current_setting
        if current_setting:
            await ctx.send("Names and nicknames will no longer be filtered.")
        else:
            await ctx.send("Names and nicknames will now be filtered.")

    async def invalidate_cache(self, guild: discord.Guild, channel: discord.TextChannel = None):
        """Invalidate a cached pattern."""
        self.pattern_cache.pop((guild, channel), None)
        if channel is None:
            for keyset in list(self.pattern_cache.keys()):  # cast needed, no remove
                if guild in keyset:
                    self.pattern_cache.pop(keyset, None)

        with contextlib.suppress(KeyError):
            del self.guild_cache[guild.id]

    async def add_to_filter(self, server_or_channel: Union[discord.Guild, discord.TextChannel], words: list) -> bool:
        added = False
        if isinstance(server_or_channel, discord.Guild):
            async with self.config.guild(server_or_channel).filter() as cur_list:
                for w in words:
                    if w.lower() not in cur_list and w:
                        cur_list.append(w.lower())
                        added = True

        elif isinstance(server_or_channel, discord.TextChannel):
            async with self.config.channel(server_or_channel).filter() as cur_list:
                for w in words:
                    if w.lower() not in cur_list and w:
                        cur_list.append(w.lower())
                        added = True

        return added

    async def remove_from_filter(self, server_or_channel: Union[discord.Guild, discord.TextChannel], words: list) -> bool:
        removed = False
        if isinstance(server_or_channel, discord.Guild):
            async with self.config.guild(server_or_channel).filter() as cur_list:
                for w in words:
                    if w.lower() in cur_list:
                        cur_list.remove(w.lower())
                        removed = True

        elif isinstance(server_or_channel, discord.TextChannel):
            async with self.config.channel(server_or_channel).filter() as cur_list:
                for w in words:
                    if w.lower() in cur_list:
                        cur_list.remove(w.lower())
                        removed = True

        return removed

    async def filter_hits(self, text: str, server_or_channel: Union[discord.Guild, discord.TextChannel]) -> set[str]:
        try:
            guild = server_or_channel.guild
            channel = server_or_channel
        except AttributeError:
            guild = server_or_channel
            channel = None

        hits: set[str] = set()

        try:
            pattern = self.pattern_cache[(guild, channel)]
        except KeyError:
            word_list = set(await self.config.guild(guild).filter())
            if channel:
                word_list |= set(await self.config.channel(channel).filter())

            pattern = re.compile("|".join(f"\\b{re.escape(w)}\\b" for w in word_list), flags=re.I) if word_list else None

            self.pattern_cache[(guild, channel)] = pattern

        if pattern:
            hits |= set(pattern.findall(text))
        return hits

    async def check_filter(self, message: discord.Message):
        # sourcery no-metrics

        hits = await self.filter_hits(message.content, message.channel)

        if not hits:
            return

        with contextlib.suppress(discord.HTTPException):
            await message.delete()
        guild = message.guild
        author = message.author
        guild_data = await self.get_guild_data(guild.id)
        member_data = await self.config.member(author).all()
        filter_count = guild_data["filterban_count"]
        filter_time = guild_data["filterban_time"]
        user_count = member_data["filter_count"]
        next_reset_time = member_data["next_reset_time"]
        created_at = message.created_at.replace(tzinfo=timezone.utc)

        if filter_count > 0 and filter_time > 0 and created_at.timestamp() >= next_reset_time:
            next_reset_time = created_at.timestamp() + filter_time
            async with self.config.member(author).all() as member_data:
                member_data["next_reset_time"] = next_reset_time
                if user_count > 0:
                    user_count = 0
                    member_data["filter_count"] = user_count

            await modlog.create_case(
                bot=self.bot,
                guild=guild,
                created_at=message.created_at.replace(tzinfo=timezone.utc),
                action_type="filterhit",
                user=author,
                moderator=guild.me,
                reason=(f"Filtered words used: {humanize_list(list(hits))}" if len(hits) > 1 else f"Filtered word used: {list(hits)[0]}"),
                channel=message.channel,
            )

            self.bot.dispatch("filter_message_delete", message, hits)
            if filter_count > 0 and filter_time > 0:
                user_count += 1
                await self.config.member(author).filter_count.set(user_count)
                if user_count >= filter_count and created_at.timestamp() < next_reset_time:
                    reason = "Autoban (too many filtered messages.)"
                    try:
                        await guild.ban(author, reason=reason)
                    except discord.HTTPException:
                        pass
                    else:
                        await modlog.create_case(self.bot, guild, message.created_at.replace(tzinfo=timezone.utc), "filterban", author, guild.me, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        author = message.author
        valid_user = isinstance(author, discord.Member) and not author.bot
        if not valid_user:
            return

        await self.check_filter(message)

    @commands.Cog.listener()
    async def on_message_edit(self, _prior, message):
        # message content has to change for non-bot's currently.
        # if this changes, we should compare before passing it.
        await self.on_message(message)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.bot.is_ready():
            return
        await self.maybe_filter_name(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.maybe_filter_name(member)

    #
    async def get_guild_data(self, guild_id: int):
        """Get the guild configuration."""
        if guild_id not in self.guild_cache:
            self.guild_cache[guild_id] = await self.config.guild_from_id(guild_id).all()
        return self.guild_cache[guild_id]

    async def maybe_filter_name(self, member: discord.Member):
        guild: discord.Guild = member.guild

        # if await self.bot.is_automod_immune(member):
        guild_data = await self.get_guild_data(guild.id)
        if not guild_data:
            return

        if guild_data["filter_names"] and await self.filter_hits(member.display_name, member.guild):
            name_to_use = guild_data["filter_default_name"]
            reason = "Filtered nickname" if member.nick else ("Filtered name")

            if member.top_role >= member.guild.me.top_role:
                return
            if not guild.me.guild_permissions.administrator:
                return
            with contextlib.suppress(discord.HTTPException):
                await member.edit(nick=name_to_use, reason=reason)

        if guild_data["filter_statuses"]:
            if hasattr(member.activity, "name"):

                async def remove_roles():
                    for rid in guild_data["status_remove_roles"]:
                        role = guild.get_role(rid)
                        if role and role in member.roles:
                            await member.remove_roles(role)

                if await self.filter_hits(str(member.activity.name), member.guild):
                    if member.top_role >= member.guild.me.top_role:
                        return
                    await remove_roles()
            word_list = set(await self.config.guild(guild).filter())
            if member.top_role >= member.guild.me.top_role:
                return
            for word in word_list:
                if member.activity and word in str(member.activity.name):
                    await remove_roles()
