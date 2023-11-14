from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Optional

import discord
import regex as re
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.utils.chat_formatting import humanize_timedelta, pagify
from melaniebot.core.utils.menus import start_adding_reactions
from melaniebot.core.utils.predicates import MessagePredicate, ReactionPredicate
from regex.regex import Pattern

from melanie import create_task

from .converters import MULTI_RE, TIME_RE, PollOptions
from .polls import Poll

EMOJI_RE: Pattern[str] = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")


class ReactPoll(commands.Cog):
    """Commands for Reaction Polls."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=1148673908, force_registration=True)
        default_guild_settings = {"polls": {}, "embed": True}
        self.conf.register_guild(**default_guild_settings)
        self.conf.register_global(polls=[])
        self.polls: dict[int, dict[int, Poll]] = {}
        self.migrate = create_task(self.migrate_old_polls())
        self.loop = create_task(self.load_polls())
        self.poll_task = create_task(self.poll_closer())
        self.close_loop = True

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle votes for polls."""
        await self.bot.wait_until_red_ready()
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        if guild.id not in self.polls:
            return
        if payload.message_id not in self.polls[guild.id]:
            return
        poll = self.polls[guild.id][payload.message_id]
        await poll.add_vote(payload.user_id, str(payload.emoji))

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle votes for polls."""
        await self.bot.wait_until_red_ready()
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        if guild.id not in self.polls:
            return
        if payload.message_id not in self.polls[guild.id]:
            return
        poll = self.polls[guild.id][payload.message_id]
        await poll.remove_vote(payload.user_id, str(payload.emoji))

    def cog_unload(self) -> None:
        self.close_loop = False
        self.poll_task.cancel()

    async def poll_closer(self) -> None:
        await self.bot.wait_until_red_ready()
        while self.close_loop:
            # consider making < 60 second polls not use config + this task
            await asyncio.sleep(5)
            now_time = datetime.utcnow()
            count = 0
            try:
                for g_id, polls in self.polls.items():
                    to_remove = []
                    for m_id, poll in polls.items():
                        if isinstance(poll.end_time, float):
                            poll.end_time = datetime.utcfromtimestamp(poll.end_time)
                        if isinstance(poll.end_time, int):
                            poll.end_time = datetime.utcfromtimestamp(poll.end_time)
                        if poll.end_time and poll.end_time <= now_time:
                            log.debug("ending poll")
                            with contextlib.suppress(Exception):
                                await poll.close_poll()

                            # probs a better way to do this
                            to_remove.append(m_id)
                            # also need to delete from config
                            guild = discord.Object(id=g_id)
                            await self.delete_poll(guild, poll)
                        if count // 10:
                            count = 0
                            await self.store_poll(poll)
                        else:
                            count += 1
                    for m_id in to_remove:
                        del self.polls[g_id][m_id]
            except Exception:
                log.error("Error checking for ended polls")

    async def delete_poll(self, guild: discord.Guild, poll: Poll) -> None:
        async with self.conf.guild(guild).polls() as polls:
            if str(poll.message_id) in polls:
                del polls[str(poll.message_id)]

    async def store_poll(self, poll: Poll) -> None:
        try:
            async with self.conf.guild(poll.guild).polls() as polls:
                polls[str(poll.message_id)] = poll.as_dict()
        except AttributeError:
            # The guild no longer exists or the channel was deleted.
            return

    async def load_polls(self) -> None:
        # unfortunately we have to deal with an issue where JSON
        # serialization fails if the config default list is used
        all_polls = await self.conf.all_guilds()

        for g_id, polls in all_polls.items():
            if g_id not in self.polls:
                self.polls[g_id] = {}
            for m_id, poll in polls["polls"].items():
                self.polls[g_id][int(m_id)] = Poll(self.bot, **poll)

    async def migrate_old_polls(self) -> None:
        try:
            polls = await self.conf.polls()
        except AttributeError:
            log.error("Error migrating old poll")
            return
        for poll in polls:
            poll["author_id"] = poll["author"]
            poll["message_id"] = poll["message"]
            poll["channel_id"] = poll["channel"]
            new_poll = Poll(self.bot, **poll)
            if not new_poll.channel:
                continue
            old_poll_msg = await new_poll.get_message()
            move_msg = f"Hello, due to a upgrade in the reaction poll cog one of your polls is no longer compatible and cannot be automatically tallied. If you wish to continue the poll, it is recommended to create a new one or manually tally the results. The poll can be found at {old_poll_msg.jump_url}"
            if new_poll.author:
                with contextlib.suppress(discord.errors.Forbidden):
                    await new_poll.author.send(move_msg)

        await self.conf.polls.clear()

    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_messages=True)
    async def rpollset(self, ctx: commands.Context) -> None:
        """Settings for reaction polls."""

    @rpollset.command(name="embed", aliases=["embeds"])
    async def rpoll_set_embed(self, ctx: commands.Context) -> None:
        """Toggle embed usage for polls in this server."""
        curr_setting = await self.conf.guild(ctx.guild).embed()
        await self.conf.guild(ctx.guild).embed.set(not curr_setting)
        verb = "off" if curr_setting else "on"
        await ctx.send(f"Reaction poll embeds turned {verb}.")

    @commands.group()
    @commands.guild_only()
    async def rpoll(self, ctx: commands.Context) -> None:
        """Commands for setting up reaction polls."""

    @rpoll.command(name="end", aliases=["close"])
    async def end_poll(self, ctx: commands.Context, poll_id: int):
        """Manually end a poll.

        `<poll_id>` is the message ID for the poll.

        """
        if ctx.guild.id not in self.polls:
            return await ctx.send("There are no polls on this server.")
        if poll_id not in self.polls[ctx.guild.id]:
            return await ctx.send("That is not a valid poll message ID.")
        poll = self.polls[ctx.guild.id][poll_id]
        await poll.close_poll()
        await ctx.tick()

    async def handle_pagify(self, ctx: commands.Context, msg: str) -> None:
        for page in pagify(msg):
            await ctx.send(page)

    @rpoll.command(name="interactive")
    async def rpoll_interactive(self, ctx: commands.Context, channel: discord.TextChannel):
        """Interactive reaction poll creator.

        Provide the channel to send the poll to. [botname] will ask you
        what the poll question will be and then ask you to provide
        options for the poll including emojis to be used.

        """
        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(f"I do not have permission to send messages in {channel.mention}")
        default_emojis = ReactionPredicate.NUMBER_EMOJIS + ReactionPredicate.ALPHABET_EMOJIS
        poll_options = {"emojis": {}, "options": [], "interactive": True, "author_id": ctx.author.id, "channel_id": channel.id}

        await ctx.send("Enter the poll question. Entering `exit` at any time will end poll creation.")
        interactive = True
        count = 0
        while interactive:
            try:
                msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=30)
            except TimeoutError:
                await ctx.send("Poll creation ended due to timeout.")
                return
            if msg.content == "exit":
                interactive = False
                break
            if not msg.content:
                if msg.attachments:
                    await ctx.send("Polls cannot handle attachments. Try again.")
                continue
            if count > 20:
                await ctx.send("Maximum number of options provided.")
                interactive = False
                continue
            if count == 0:
                if not msg.content.endswith("?"):
                    await ctx.send("That doesn't look like a question, try again.")
                else:
                    poll_options["question"] = msg.content
                    await ctx.send(
                        "Enter the options for the poll. Enter an emoji at the beginning of the message if you want to use custom emojis for the option counters.",
                    )
                    count += 1
                continue
            custom_emoji = EMOJI_RE.match(msg.content)
            time_match = TIME_RE.match(msg.content)
            if multi_match := MULTI_RE.match(msg.content):
                poll_options["multiple_votes"] = True
                await ctx.send("Allowing multiple votes for this poll.")
                continue
            if time_match:
                time_data = {}
                for time in TIME_RE.finditer(msg.content):
                    for k, v in time.groupdict().items():
                        if v:
                            time_data[k] = int(v)
                poll_options["duration"] = timedelta(**time_data)
                await ctx.send(f"Duration for the poll set to {humanize_timedelta(timedelta=poll_options['duration'])}")
                continue
            if custom_emoji:
                if custom_emoji.group(0) in poll_options["emojis"]:
                    await ctx.send("That emoji option is already being used.")
                    continue
                try:
                    await msg.add_reaction(custom_emoji.group(0))
                    poll_options["emojis"][custom_emoji.group(0)] = msg.content.replace(custom_emoji.group(0), "")
                    await ctx.send(f"Option {custom_emoji.group(0)} set to {msg.content.replace(custom_emoji.group(0), '')}")
                    poll_options["options"].append(msg.content.replace(custom_emoji.group(0), ""))
                except Exception:
                    poll_options["emojis"][default_emojis[count]] = msg.content
                    poll_options["options"].append(msg.content)
                    await self.handle_pagify(ctx, f"Option {default_emojis[count]} set to {msg.content}")
            else:
                try:
                    maybe_emoji = msg.content.split(" ")[0]
                    if maybe_emoji in poll_options["emojis"]:
                        await ctx.send("That emoji option is already being used.")
                        continue
                    await msg.add_reaction(maybe_emoji)
                    poll_options["emojis"][maybe_emoji] = " ".join(msg.content.split(" ")[1:])
                    poll_options["options"].append(" ".join(msg.content.split(" ")[1:]))
                    await self.handle_pagify(ctx, f"Option {maybe_emoji} set to {' '.join(msg.content.split(' ')[1:])}")
                except Exception:
                    poll_options["emojis"][default_emojis[count]] = msg.content
                    poll_options["options"].append(msg.content)
                    await self.handle_pagify(ctx, f"Option {default_emojis[count]} set to {msg.content}")
            count += 1
            continue
        if not poll_options["emojis"]:
            return await ctx.send("No poll created.")
        new_poll = Poll(self.bot, **poll_options)
        (text, em) = await new_poll.build_poll()
        if new_poll.embed:
            sample_msg = await ctx.send("Is this poll good?", embed=em)
        else:
            for page in pagify(f"Is this poll good?\n\n{text}"):
                sample_msg = await ctx.send(page)
        start_adding_reactions(sample_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = ReactionPredicate.yes_or_no(sample_msg, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred)
        except TimeoutError:
            await ctx.send("Not making poll.")
            return
        if pred.result:
            await new_poll.open_poll()
            if ctx.guild.id not in self.polls:
                self.polls[ctx.guild.id] = {}
            self.polls[ctx.guild.id][new_poll.message_id] = new_poll
            await self.store_poll(new_poll)
        else:
            await ctx.send("Not making poll.")

    @rpoll.command(name="new", aliases=["create"])
    async def rpoll_create(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *, poll_options: PollOptions):
        """Start a reaction poll.

        `[channel]` is the optional channel you want to send the poll
        to. If no channel is provided it will default to the current
        channel. `<poll_options>` is a formatted string of poll options.
        The question is everything before the first occurance of `?`.
        The options are a list separated by `;`. The time the poll ends
        is a space separated list of units of time. if `multi-vote` is
        provided anywhere in the creation message the poll will allow
        users to vote on multiple choices.

        Example format (time argument is optional): `;rpoll new Is this
        a poll? Yes;No;Maybe; 2 hours 21 minutes 40 seconds multi-vote`

        """
        send_channel = channel or ctx.channel
        if not send_channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(f"I do not have permission to send messages in {send_channel.mention}")
        poll_options["channel_id"] = send_channel.id
        # allow us to specify new channel for the poll

        guild = ctx.guild
        embed = await self.conf.guild(guild).embed() and send_channel.permissions_for(ctx.me).embed_links
        poll_options["embed"] = embed
        poll = Poll(self.bot, **poll_options)

        await poll.open_poll()
        if guild.id not in self.polls:
            self.polls[guild.id] = {}
        self.polls[guild.id][poll.message_id] = poll
        await self.store_poll(poll)
