from __future__ import annotations

from typing import Union

import discord
import regex as re
from discord.ext.commands.converter import IDConverter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import commands


class ChannelUserRole(IDConverter):
    """This will check to see if the provided argument is a channel, user, or
    role.

    Guidance code on how to do this from:

    """

    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.TextChannel, discord.Role, discord.Member]:
        guild = ctx.guild
        result = None
        id_match = self._get_id_match(argument)
        channel_match = re.match(r"<#([0-9]+)>$", argument)
        member_match = re.match(r"<@!?([0-9]+)>$", argument)
        role_match = re.match(r"<@&([0-9]+)>$", argument)
        for converter in ["channel", "role", "member"]:
            if converter == "channel":
                if match := id_match or channel_match:
                    channel_id = match.group(1)
                    result = guild.get_channel(int(channel_id))
                else:
                    result = discord.utils.get(guild.text_channels, name=argument)
            elif converter == "member":
                if match := id_match or member_match:
                    member_id = match.group(1)
                    result = guild.get_member(int(member_id))
                else:
                    result = guild.get_member_named(argument)
            elif converter == "role":
                if match := id_match or role_match:
                    role_id = match.group(1)
                    result = guild.get_role(int(role_id))
                else:
                    result = discord.utils.get(guild._roles.values(), name=argument)
            if result:
                break
        if not result:
            msg = f"{argument} is not a valid channel, user or role."
            raise BadArgument(msg)
        return result
