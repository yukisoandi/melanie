from __future__ import annotations

import asyncio
import textwrap
from re import Pattern
from typing import Optional, Union

import discord
import regex as re
from aiomisc import cancel_tasks
from boltons.urlutils import find_all_links
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import Config, checks, commands
from melaniebot.core.commands import BadArgument
from melaniebot.core.utils.chat_formatting import humanize_list

from fun.fun import INVITE_RE
from melanie import (
    SHARED_API_HEADERS,
    checkpoint,
    create_task,
    footer_gif,
    make_e,
    url_concat,
)
from melanie.core import spawn_task
from melanie.models.sharedapi.speech import STTResult

from .api import AzureTranslateAPI, FlagTranslation
from .converters import ChannelUserRole
from .errors import AzureTranslateAPIError

EMOJI_REGEX: Pattern = re.compile(r"(<(a)?:[a-zA-Z0-9\_]+:([0-9]+)>)")
MENTION_REGEX: Pattern = re.compile(r"<@!?([0-9]+)>")
ID_REGEX: Pattern = re.compile(r"[0-9]{17,}")
AUDIO: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:mov|ogg|mp4|wav)(\?size=[0-9]*)?)", flags=re.I)


class AudioFinder(Converter):
    """This is a class to convert notsobots image searching capabilities into a
    more general converter class.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> list[Union[discord.Asset, str]]:
        attachments = ctx.message.attachments
        MENTION_REGEX.finditer(argument)
        matches = AUDIO.finditer(argument)
        EMOJI_REGEX.finditer(argument)
        ID_REGEX.finditer(argument)
        urls = []
        if matches:
            urls.extend(match.group(1) for match in matches)

        if attachments:
            urls.extend(attachment.url for attachment in attachments)

        if not urls:
            urls = await self.search_for_images(ctx)

        if not urls:
            msg = "No Audio provided."
            raise BadArgument(msg)
        return urls

    async def search_for_images(self, ctx: commands.Context) -> list[Union[discord.Asset, discord.Attachment, str]]:
        urls = []
        if not ctx.channel.permissions_for(ctx.me).read_message_history:
            msg = "I require read message history perms to find images."
            raise BadArgument(msg)
        msg: discord.Message = ctx.message
        if msg.attachments:
            urls.extend(i.url for i in msg.attachments)
        if msg.reference:
            channel: discord.TextChannel = ctx.bot.get_channel(msg.reference.channel_id)
            ref: discord.Message = msg.reference.cached_message
            if not ref:
                ref = await channel.fetch_message(msg.reference.message_id)
            urls.extend(i.url for i in ref.attachments)
            if match := AUDIO.match(ref.content):
                urls.append(match.group(1))
        async for message in ctx.channel.history(limit=10):
            await checkpoint()
            if message.attachments:
                urls.extend(i.url for i in message.attachments)
            if match := AUDIO.match(message.content):
                urls.append(match.group(1))
        # if not urls:
        return urls


class Translate(AzureTranslateAPI, commands.Cog):
    """Translate messages using Azure Cognitive Services."""

    __version__ = "2.3.9"

    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 156434873547, force_registration=True)
        default_guild = {"reaction": True, "text": False, "whitelist": [], "blacklist": [], "count": {"characters": 0, "requests": 0, "detect": 0}}
        default = {"cooldown": {"past_flags": [], "timeout": 15, "multiple": True}, "count": {"characters": 0, "requests": 0, "detect": 0}}
        self.config.register_guild(**default_guild)
        self.config.register_global(**default)
        self.cache = {
            "translations": [],
            "cooldown_translations": {},
            "guild_messages": [],
            "guild_reactions": [],
            "cooldown": {},
            "guild_blacklist": {},
            "guild_whitelist": {},
        }
        self._key: Optional[str] = "9955e41d38fc435cba0e83bdc8b8db30"
        self._guild_counter = {}
        self._global_counter = {}
        self.active_tasks = []
        spawn_task(self.init(), self.active_tasks)

    async def init(self) -> None:
        await self.bot.waits_uptime_for(10)
        self._global_counter = await self.config.count()
        all_guilds = await self.config.all_guilds()
        for g_id, data in all_guilds.items():
            self._guild_counter[g_id] = data["count"]

    @commands.command(aliases=["tr"])
    async def translate(self, ctx: commands.Context, *, message: Optional[Union[discord.Message, str]] = None) -> None:
        """Translate messages with Azure Cognitive Services.

        Defaults to English. Use `translate2` for choosing another
        language. `<message>` is the message to translate.

        """
        async with ctx.typing(), asyncio.timeout(90):
            to_language = "en"
            author = ctx.message.author
            requestor = ctx.message.author
            if not message and ctx.message.reference:
                message = ctx.message.reference.cached_message or await ctx.channel.fetch_message(ctx.message.reference.message_id)

            if isinstance(message, discord.Message):
                author = message.author
                message = message.clean_content
            if not message:
                audio = await AudioFinder().search_for_images(ctx)

                if audio:
                    url = audio[0]

                    r = await self.bot.curl.fetch(
                        url_concat(f"https://dev.melaniebot.net/api/speech/stt?url={url}", {"user_id": str(ctx.author.id), "translate": "true"}),
                        headers=SHARED_API_HEADERS,
                    )

                    s = STTResult.parse_raw(r.body)
                    if not s.display_text:
                        if ctx.message.id in self.stt_cache:
                            return
                        return await ctx.send(embed=make_e("I couldn't find hear any text in that clip", 3))

                    text_value = s.display_text
                    text_value = text_value.capitalize()
                    for match in INVITE_RE.findall(text_value):
                        text_value = text_value.replace(match, "(redacted)")
                    for link in find_all_links(text_value):
                        text_value = text_value.replace(str(link), "(redacted)")
                    em = discord.Embed()
                    em.description = f">>> {textwrap.shorten(text_value, width=4000)}"
                    em.set_footer(text="melanie ^_^", icon_url=footer_gif)
                    try:
                        await ctx.reply(embed=em)
                    except discord.HTTPException:
                        await ctx.send(embed=em)
                    return True

                else:
                    async for m in ctx.channel.history(limit=10, before=ctx.message):
                        if m.author.bot:
                            continue
                        if m.content:
                            message = m.content
                            break

            detected_lang = await self.detect_language(message)

            from_lang = detected_lang[0]["language"]
            original_lang = detected_lang[0]["language"]
            if to_language == original_lang:
                return await ctx.send(f"I cannot translate `{from_lang}` to `{to_language}`")
            translated_text = await self.translate_text(original_lang, to_language, message)
            translation = (translated_text, from_lang, to_language)
            em = await self.translation_embed(author, translation, requestor)
            await ctx.reply(embed=em, mention_author=False)

    @commands.command(name="translate2", aliases=["tr2", "t2"])
    async def translate2(
        self,
        ctx: commands.Context,
        to_language: FlagTranslation,
        *,
        message: Optional[Union[discord.Message, commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)]],
    ) -> None:
        """Translate messages with Azure Cognitive Services.

        `<to_language>` is the language you would like to translate
        `<message>` is the message to translate.

        """
        async with ctx.typing(), asyncio.timeout(30):
            author = ctx.message.author
            requestor = ctx.message.author
            msg = ctx.message
            if not to_language:
                to_language = "en"
            if not message and ctx.message.reference:
                message = ctx.message.reference.cached_message or await ctx.channel.fetch_message(ctx.message.reference.message_id)

            if not message:
                return await ctx.send_help()
            if isinstance(message, discord.Message):
                msg = message
                author = message.author
                message = message.clean_content
            try:
                detected_lang = await self.detect_language(message)
                await self.add_detect(ctx.guild)
            except AzureTranslateAPIError as e:
                await ctx.send(str(e))
                return
            from_lang = detected_lang[0]["language"]
            original_lang = detected_lang[0]["language"]
            if to_language == original_lang:
                return await ctx.send(f"I cannot translate `{from_lang}` to `{to_language}`")
            translated_text = await self.translate_text(original_lang, to_language, message)
            await self.add_requests(ctx.guild, message)
            translation = (translated_text, from_lang, to_language)
            em = await self.translation_embed(author, translation, requestor)
        await ctx.send(embed=em, reference=msg, mention_author=False)

    @commands.group()
    async def translateset(self, ctx: commands.Context) -> None:
        """Toggle the bot auto translating."""

    @checks.is_owner()
    @translateset.command(name="stats")
    async def translate_stats(self, ctx: commands.Context, guild_id: Optional[int]):
        """Shows translation usage."""
        if guild_id and await self.bot.is_owner(ctx.author):
            if not (guild := self.bot.get_guild(guild_id)):
                return await ctx.send(f"Guild `{guild_id}` not found.")
        else:
            guild = ctx.guild
        tr_keys = {"requests": "API Requests:", "detect": "API Detect Language:", "characters": "Characters requested:"}
        count = self._guild_counter[guild.id] if guild.id in self._guild_counter else await self.config.guild(guild).count()
        gl_count = self._global_counter or await self.config.count()
        msg = "__Global Usage__:\n"
        for key, value in gl_count.items():
            msg += f"{tr_keys[key]} **{value}**\n"
        msg += f"__{guild.name} Usage__:\n"
        for key, value in count.items():
            msg += f"{tr_keys[key]} **{value}**\n"
        await ctx.maybe_send_embed(msg)

    @translateset.group(aliases=["blocklist"])
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist(self, ctx: commands.Context) -> None:
        """Set blacklist options for translations.

        blacklisting supports channels, users, or roles

        """

    @translateset.group(aliases=["allowlist"])
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist(self, ctx: commands.Context) -> None:
        """Set whitelist options for translations.

        whitelisting supports channels, users, or roles

        """

    @whitelist.command(name="add")
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_add(self, ctx: commands.Context, *channel_user_role: ChannelUserRole) -> None:
        """Add a channel, user, or role to translation whitelist."""
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be whitelisted.")
        for obj in channel_user_role:
            if obj.id not in await self.config.guild(ctx.guild).whitelist():
                async with self.config.guild(ctx.guild).whitelist() as whitelist:
                    whitelist.append(obj.id)
                await self._bw_list_cache_update(ctx.guild)
        msg = "`{list_type}` added to translation whitelist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

    @whitelist.command(name="remove", aliases=["rem", "del"])
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_remove(self, ctx: commands.Context, *channel_user_role: ChannelUserRole) -> None:
        """Remove a channel, user, or role from translation whitelist."""
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels, users, or roles to be removed from the whitelist")
        for obj in channel_user_role:
            if obj.id in await self.config.guild(ctx.guild).whitelist():
                async with self.config.guild(ctx.guild).whitelist() as whitelist:
                    whitelist.remove(obj.id)
                await self._bw_list_cache_update(ctx.guild)
        msg = "`{list_type}` removed from translation whitelist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

    @whitelist.command(name="list")
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_list(self, ctx: commands.Context) -> None:
        """List Channels, Users, and Roles in the servers translation whitelist."""
        whitelist = []
        for _id in await self.config.guild(ctx.guild).whitelist():
            try:
                whitelist.append(await ChannelUserRole().convert(ctx, str(_id)))
            except BadArgument:
                continue
        whitelist_s = ", ".join(x.name for x in whitelist)
        await ctx.send(f"`{whitelist_s}` are currently whitelisted.")

    @blacklist.command(name="add")
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_add(self, ctx: commands.Context, *channel_user_role: ChannelUserRole) -> None:
        """Add a channel, user, or role to translation blacklist."""
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels users or roles to be blacklisted.")
        for obj in channel_user_role:
            if obj.id not in await self.config.guild(ctx.guild).blacklist():
                async with self.config.guild(ctx.guild).blacklist() as blacklist:
                    blacklist.append(obj.id)
                await self._bw_list_cache_update(ctx.guild)
        msg = "`{list_type}` added to translation blacklist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

    @blacklist.command(name="remove", aliases=["rem", "del"])
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_remove(self, ctx: commands.Context, *channel_user_role: ChannelUserRole) -> None:
        """Remove a channel, user, or role from translation blacklist."""
        if not channel_user_role:
            return await ctx.send("You must supply 1 or more channels, users, or roles to be removed from the blacklist")
        for obj in channel_user_role:
            if obj.id in await self.config.guild(ctx.guild).blacklist():
                async with self.config.guild(ctx.guild).blacklist() as blacklist:
                    blacklist.remove(obj.id)
                await self._bw_list_cache_update(ctx.guild)
        msg = "`{list_type}` removed from translation blacklist."
        list_type = humanize_list([c.name for c in channel_user_role])
        await ctx.send(msg.format(list_type=list_type))

    @blacklist.command(name="list")
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_list(self, ctx: commands.Context) -> None:
        """List Channels, Users, and Roles in the servers translation blacklist."""
        blacklist = []
        for _id in await self.config.guild(ctx.guild).blacklist():
            try:
                blacklist.append(await ChannelUserRole().convert(ctx, str(_id)))
            except BadArgument:
                continue
        blacklist_s = ", ".join(x.name for x in blacklist)
        await ctx.send(f"`{blacklist_s}` are currently blacklisted.")

    @translateset.command(aliases=["reaction", "reactions"])
    @checks.mod_or_permissions(manage_channels=True)
    @commands.guild_only()
    async def react(self, ctx: commands.Context) -> None:
        """Toggle translations to flag emoji reactions."""
        guild = ctx.message.guild
        toggle = not await self.config.guild(guild).reaction()
        if toggle:
            verb = "on"
        else:
            verb = "off"
            if guild.id in self.cache["guild_reactions"]:
                self.cache["guild_reactions"].remove(guild.id)
        await self.config.guild(guild).reaction.set(toggle)
        msg = "Reaction translations have been turned "
        await ctx.send(msg + verb)

    @translateset.command(aliases=["multi"])
    @checks.is_owner()
    @commands.guild_only()
    async def multiple(self, ctx: commands.Context) -> None:
        """Toggle multiple translations for the same message.

        This will also ignore the translated message from being
        translated into another language

        """
        toggle = not await self.config.cooldown.multiple()
        await self.config.cooldown.multiple.set(toggle)
        self.cache["cooldown"] = await self.config.cooldown()
        verb = "on" if toggle else ("off")
        await ctx.send(f"Multiple translations have been turned {verb}")

    @translateset.command(aliases=["cooldown"])
    @checks.is_owner()
    @commands.guild_only()
    async def timeout(self, ctx: commands.Context, time: int) -> None:
        """Set the cooldown before a message can be reacted to again for
        translation.

        `<time>` Number of seconds until that message can be reacted to again
        Note: If multiple reactions are not allowed the timeout setting
        is ignored until the cache cleanup ~10 minutes.

        """
        await self.config.cooldown.timeout.set(time)
        self.cache["cooldown"] = await self.config.cooldown()
        msg = f"Translation timeout set to {time}s."
        await ctx.send(msg)

    @translateset.command(aliases=["flags"])
    @checks.mod_or_permissions(manage_channels=True)
    @commands.guild_only()
    async def flag(self, ctx: commands.Context) -> None:
        """Toggle translations with flag emojis in text."""
        guild = ctx.message.guild
        toggle = not await self.config.guild(guild).text()
        if toggle:
            verb = "on"
        else:
            verb = "off"
            if guild.id in self.cache["guild_messages"]:
                self.cache["guild_messages"].remove(guild.id)
        await self.config.guild(guild).text.set(toggle)
        msg = "Flag emoji translations have been turned "
        await ctx.send(msg + verb)

    def cog_unload(self) -> None:
        cancel_tasks(self.active_tasks)
        create_task(self._save_usage_stats())
