"""MIT License.

Copyright (c) 2020-present phenom4n4n

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import re

import discord
from melaniebot.core import commands

from melanie import log

link_regex = re.compile(
    r"https?:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/channels\/(?P<guild_id>[0-9]{15,19})\/(?P<channel_id>[0-9]{15,19})\/(?P<message_id>[0-9]{15,19})\/?",
)


class LinkToMessage(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Message:
        match = re.search(link_regex, argument)
        if not match:
            raise commands.MessageNotFound(argument)

        with log.catch(reraise=True):
            guild_id = int(match["guild_id"])
            channel_id = int(match["channel_id"])
            message_id = int(match["message_id"])

            guild = ctx.bot.get_guild(guild_id)
            if not guild:
                raise commands.GuildNotFound(guild_id)

            channel = guild.get_channel(channel_id)
            if not channel:
                raise commands.ChannelNotFound(channel_id)

            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound as e:
                raise commands.MessageNotFound(argument) from e
            except discord.Forbidden as e:
                msg = f"Can't read messages in {channel.mention}."
                raise commands.BadArgument(msg) from e
            else:
                return await self.validate_message(ctx, message)

    async def validate_message(self, ctx: commands.Context, message: discord.Message) -> discord.Message:
        with log.catch(reraise=True):
            if not message.guild:
                msg = "I can only quote messages from servers."
                raise commands.BadArgument(msg)
            guild = message.guild
            if message.channel.nsfw and not ctx.channel.nsfw:
                msg = "Messages from NSFW channels cannot be quoted in non-NSFW channels."
                raise commands.BadArgument(msg)

            cog = ctx.bot.get_cog("LinkQuoter")
            data = await cog.config.guild(ctx.guild).all()

            if guild.id != ctx.guild.id:
                guild_data = await cog.config.guild(guild).all()
                if not data["cross_server"]:
                    msg = "This server is not opted in to quote messages from other servers."
                    raise commands.BadArgument(msg)
                elif not guild_data["cross_server"]:
                    msg = "That server is not opted in to allow its messages to be quoted in other servers."
                    raise commands.BadArgument(msg)

            # if member := guild.get_member(ctx.author.id):
            #     if not (author_perms.read_message_history and author_perms.read_messages):
            return message
