from __future__ import annotations

import discord

from melaniebot.core import commands

from .abc import MixinMeta  # type: ignore


class Events(MixinMeta):
    """This is a mixin for the core mod cog Has a bunch of things split off to
    here.
    """

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.name != after.name:
            if "afk" in before.name.lower():
                return
            async with self.config.user(before).past_names() as name_list:
                while None in name_list:  # clean out null entries from a bug
                    name_list.remove(None)
                if before.name in name_list:
                    # Ensure order is maintained without duplicates occurring
                    name_list.remove(before.name)
                name_list.append(before.name)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick == after.nick or before.nick is None:
            return
        if "afk" in before.nick.lower():
            return
        async with self.config.member(before).past_nicks() as nick_list:
            while None in nick_list:  # clean out null entries from a bug
                nick_list.remove(None)
            bad_str = ("hatesgays", "afk", "thispussybelongstomelanie", "belongstomelanie", "CHANGEME")
            if before.nick in nick_list:
                nick_list.remove(before.nick)
            for n in nick_list:
                if any(s.lower() in n.lower() for s in bad_str):
                    nick_list.remove(n)
            nick_list.append(before.nick)
