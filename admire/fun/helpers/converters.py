from __future__ import annotations

from re import Pattern
from typing import Union

import discord
import regex as re
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import commands
from melaniebot.core.commands import BadArgument

from melanie import checkpoint

VIDEO_MIMES = {
    "video/3gpp": ".3gpp",
    "video/3gpp2": ".3gp2",
    "video/annodex": ".axv",
    "video/divx": ".divx",
    "video/mp4": ".mp4v",
    "video/mpeg": ".vbk",
    "video/ogg": ".ogv",
    "video/quicktime": ".qt",
    "video/vnd.dlna.mpeg-tts": ".tts",
    "video/webm": ".webm",
    "video/x-dv": ".dv",
    "video/x-flv": ".flv",
    "video/x-ivf": ".IVF",
    "video/x-la-asf": ".lsx",
    "video/x-m4v": ".m4v",
    "video/x-matroska-3d": ".mk3d",
    "video/x-matroska": ".mkv",
    "video/x-ms-asf": ".nsc",
    "video/x-ms-wm": ".wm",
    "video/x-ms-wmp": ".wmp",
    "video/x-ms-wmv": ".wmv",
    "video/x-ms-wmx": ".wmx",
    "video/x-ms-wvx": ".wvx",
    "video/x-msvideo": ".avi",
    "video/x-sgi-movie": ".movie",
}

exts = [str(x.split(".")[1]) for x in VIDEO_MIMES.values()]

IMAGE_LINKS: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg|webp|gif|png|svg)(\?size=[0-9]*)?)", flags=re.I)
EMOJI_REGEX: Pattern = re.compile(r"(<(a)?:[a-zA-Z0-9\_]+:([0-9]+)>)")
MENTION_REGEX: Pattern = re.compile(r"<@!?([0-9]+)>")
ID_REGEX: Pattern = re.compile(r"[0-9]{17,}")
VIDEO_LINKS: Pattern = re.compile(r"(https?:\/\/[^\"\'\s]*\.(?:mp4|mp4a|ogg|m4a|mp3|oga|opus|mov|aac|wav|flac|wma|avi|opus)(\?size=[0-9]*)?)", flags=re.I)


class AudioVideoFindeer(Converter):
    """This is a class to convert notsobots image searching capabilities into a
    more general converter class.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> list[Union[discord.Asset, str]]:
        attachments = ctx.message.attachments
        MENTION_REGEX.finditer(argument)
        matches = VIDEO_LINKS.finditer(argument)
        EMOJI_REGEX.finditer(argument)
        ID_REGEX.finditer(argument)
        urls = []
        if matches:
            urls.extend(match.group(1) for match in matches)
        if attachments:
            urls.extend(attachment.url for attachment in attachments)
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
            if match := VIDEO_LINKS.match(ref.content):
                urls.append(match.group(1))

        async for message in ctx.channel.history(limit=20):
            await checkpoint()
            message: discord.Message
            for attachment in message.attachments:
                _url = str(attachment.url)
                if VIDEO_LINKS.match(_url):
                    urls.append(_url)

            if match := VIDEO_LINKS.match(message.content):
                urls.append(match.group(1))
        if not urls:
            raise BadArgument("No Images found in recent history.")
        return urls
