from __future__ import annotations

from re import Pattern
from typing import Union

import discord
import regex as re
import unidecode
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import commands
from melaniebot.core.commands import BadArgument, MemberConverter
from rapidfuzz import process

from melanie import checkpoint

IMAGE_LINKS: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg|webp|gif|png|svg)(\?size=[0-9]*)?)", flags=re.I)
EMOJI_REGEX: Pattern = re.compile(r"(<(a)?:[a-zA-Z0-9\_]+:([0-9]+)>)")
MENTION_REGEX: Pattern = re.compile(r"<@!?([0-9]+)>")
ID_REGEX: Pattern = re.compile(r"[0-9]{17,}")
VIDEO_LINKS: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg|mov|mp4|webv|webp|gif|png|svg)(\?size=[0-9]*)?)", flags=re.I)


class FuzzyMember(MemberConverter):
    def __init__(self, response: bool = True) -> None:
        self.response = response
        super().__init__()

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Role:
        try:
            member = await super().convert(ctx, argument)
        except BadArgument as e:
            guild = ctx.guild
            result = [
                (m[2], m[1]) for m in process.extract(argument, {m: unidecode.unidecode(m.display_name) for m in guild.members}, limit=None, score_cutoff=75)
            ]
            if not result:
                raise BadArgument(f'Member "{argument}" not found.' if self.response else None) from e

            sorted_result = sorted(result, key=lambda r: r[1], reverse=True)
            member = sorted_result[0][0]
        return member


class ImageFinder(Converter):
    """This is a class to convert notsobots image searching capabilities into a
    more general converter class.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> list[Union[discord.Asset, str]]:
        attachments = ctx.message.attachments
        mentions = MENTION_REGEX.finditer(argument)
        matches = IMAGE_LINKS.finditer(argument)
        emojis = EMOJI_REGEX.finditer(argument)
        ids = ID_REGEX.finditer(argument)
        urls = []
        if matches:
            urls.extend(match.group(1) for match in matches)
        if emojis:
            for emoji in emojis:
                ext = "gif" if emoji.group(2) else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji.group(3)}.{ext}?v=1"
                urls.append(url)
        if mentions:
            for mention in mentions:
                user = ctx.guild.get_member(int(mention.group(1)))
                if user.is_avatar_animated():
                    urls.append(user.avatar_url_as(format="gif"))
                else:
                    urls.append(user.avatar_url_as(format="png"))
        if not urls and ids:
            for possible_id in ids:
                if user := ctx.guild.get_member(int(possible_id.group(0))):
                    if user.is_avatar_animated():
                        urls.append(user.avatar_url_as(format="gif"))
                    else:
                        urls.append(user.avatar_url_as(format="png"))
        if attachments:
            urls.extend(attachment.url for attachment in attachments)
        if not urls:
            for m in ctx.guild.members:
                m: discord.Member
                await checkpoint()
                if argument.lower() in unidecode.unidecode(m.display_name.lower()):
                    # display_name so we can get the nick of the user first
                    # without being NoneType and then check username if that matches
                    # what we're expecting
                    urls.append(m.avatar_url_as(format="png"))
                    continue
                if argument.lower() in unidecode.unidecode(m.name.lower()):
                    urls.append(m.avatar_url_as(format="png"))
                    continue
        if not urls:
            urls = await self.search_for_images(ctx)

        if not urls:
            msg = "No images provided."
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
            if match := IMAGE_LINKS.match(ref.content):
                urls.append(match.group(1))
        async for message in ctx.channel.history(limit=50):
            await checkpoint()
            if message.attachments:
                urls.extend(i.url for i in message.attachments)
            if match := IMAGE_LINKS.match(message.content):
                urls.append(match.group(1))
        if not urls:
            raise BadArgument("No Images found in recent history.")
        return urls


class VideoFinder(ImageFinder):
    async def convert(self, ctx: commands.Context, argument: str) -> list[Union[discord.Asset, str]]:
        attachments = ctx.message.attachments
        mentions = MENTION_REGEX.finditer(argument)
        matches = VIDEO_LINKS.finditer(argument)
        emojis = EMOJI_REGEX.finditer(argument)
        ids = ID_REGEX.finditer(argument)
        urls = []
        if matches:
            urls.extend(match.group(1) for match in matches)
        if emojis:
            for emoji in emojis:
                ext = "gif" if emoji.group(2) else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji.group(3)}.{ext}?v=1"
                urls.append(url)
        if mentions:
            for mention in mentions:
                user = ctx.guild.get_member(int(mention.group(1)))
                if user.is_avatar_animated():
                    urls.append(user.avatar_url_as(format="gif"))
                else:
                    urls.append(user.avatar_url_as(format="png"))
        if not urls and ids:
            for possible_id in ids:
                if user := ctx.guild.get_member(int(possible_id.group(0))):
                    if user.is_avatar_animated():
                        urls.append(user.avatar_url_as(format="gif"))
                    else:
                        urls.append(user.avatar_url_as(format="png"))
        if attachments:
            urls.extend(attachment.url for attachment in attachments)
        if not urls:
            for m in ctx.guild.members:
                m: discord.Member
                await checkpoint()
                if argument.lower() in unidecode.unidecode(m.display_name.lower()):
                    # display_name so we can get the nick of the user first
                    # without being NoneType and then check username if that matches
                    # what we're expecting
                    urls.append(m.avatar_url_as(format="png"))
                    continue
                if argument.lower() in unidecode.unidecode(m.name.lower()):
                    urls.append(m.avatar_url_as(format="png"))
                    continue

        if not urls:
            msg = "No images provided."
            raise BadArgument(msg)
        return urls
