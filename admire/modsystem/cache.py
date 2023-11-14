from __future__ import annotations

import contextlib
from typing import Optional

import discord
import regex as re
from loguru import logger as log
from melaniebot.core import Config
from melaniebot.core.bot import Melanie

from melanie import alru_cache


class MemoryCache:
    """This class is used to store most used Config values and reduce calls for
    optimization.

    See Github issue #49

    """

    def __init__(self, bot: Melanie, config: Config) -> None:
        self.bot = bot
        self.data = config

        self.mute_roles = {}
        self.temp_actions = {}
        self.automod_enabled = []
        self.automod_antispam = {}
        self.automod_regex = {}

    async def init_automod_enabled(self) -> None:
        for guild_id, data in (await self.data.all_guilds()).items():
            with contextlib.suppress(KeyError):
                if data["automod"]["enabled"] is True:
                    self.automod_enabled.append(guild_id)

    async def _debug_info(self) -> str:
        """Compare the cached data to the Config data. Text is logged (INFO) then
        returned.

        This calls a huge part of the Config database and will not load
        it into the cache.

        """
        config_data = await self.data.all_guilds()
        mute_roles_cached = len(self.mute_roles)
        mute_roles = len([x for x in config_data.values() if x["mute_role"] is not None])
        guild_temp_actions_cached = len(self.temp_actions)
        guild_temp_actions = len([x for x in config_data.values() if x["temporary_warns"]])
        temp_actions_cached = sum(len(x) for x in self.temp_actions.values())
        temp_actions = sum(len(x["temporary_warns"]) for x in config_data.values())
        text = f"Debug info requested\n{mute_roles_cached}/{mute_roles} mute roles loaded in cache.\n{guild_temp_actions_cached}/{guild_temp_actions} guilds with temp actions loaded in cache.\n{temp_actions_cached}/{temp_actions} temporary actions loaded in cache."
        log.info(text)
        return text

    async def get_mute_role(self, guild: discord.Guild):
        role_id = self.mute_roles.get(guild.id)
        if role_id is ...:
            return None
        if not role_id:
            role_id = await self.data.guild(guild).mute_role()
            self.mute_roles[guild.id] = role_id or ...
        return role_id

    async def update_mute_role(self, guild: discord.Guild, role: discord.Role) -> None:
        await self.data.guild(guild).mute_role.set(role.id)
        self.mute_roles[guild.id] = role.id

    async def get_temp_action(self, guild: discord.Guild, member: Optional[discord.Member] = None):
        guild_temp_actions = self.temp_actions.get(guild.id, {})
        if not guild_temp_actions:
            guild_temp_actions = await self._get_guild_temp_actions(guild.id)
            if guild_temp_actions:
                self.temp_actions[guild.id] = guild_temp_actions
        if member is None:
            return guild_temp_actions
        return guild_temp_actions.get(member.id)

    async def add_temp_action(self, guild: discord.Guild, member: discord.Member, data: dict) -> None:
        await self.data.guild(guild).temporary_warns.set_raw(member.id, value=data)
        try:
            guild_temp_actions = self.temp_actions[guild.id]
        except KeyError:
            self.temp_actions[guild.id] = {member.id: data}
        else:
            guild_temp_actions[member.id] = data

        self._get_guild_temp_actions.cache_clear()

    @alru_cache(maxsize=None, ttl=320)
    async def _get_guild_temp_actions(self, gid: int):
        return await self.data.guild_from_id(gid).temporary_warns.all()

    async def remove_temp_action(self, guild: discord.Guild, member: discord.Member) -> None:
        await self.data.guild(guild).temporary_warns.clear_raw(member.id)
        with contextlib.suppress(KeyError):
            del self.temp_actions[guild.id][member.id]

        self._get_guild_temp_actions.cache_clear()

    async def bulk_remove_temp_action(self, guild: discord.Guild, members: list) -> None:
        members = [x.id for x in members]
        warns = await self.get_temp_action(guild)
        warns = {x: y for x, y in warns.items() if int(x) not in members}
        await self.data.guild(guild).temporary_warns.set(warns)
        self.temp_actions[guild.id] = warns
        self._get_guild_temp_actions.cache_clear()

    def is_automod_enabled(self, guild: discord.Guild) -> bool:
        return guild.id in self.automod_enabled

    async def add_automod_enabled(self, guild: discord.Guild) -> None:
        self.automod_enabled.append(guild.id)
        await self.data.guild(guild).automod.enabled.set(True)

    async def remove_automod_enabled(self, guild: discord.Guild) -> None:
        self.automod_enabled.remove(guild.id)
        await self.data.guild(guild).automod.enabled.set(False)

    async def get_automod_antispam(self, guild: discord.Guild):
        automod_antispam = self.automod_antispam.get(guild.id, None)
        if automod_antispam is not None:
            return automod_antispam
        automod_antispam = await self.data.guild(guild).automod.antispam.all()
        if automod_antispam["enabled"] is False:
            self.automod_antispam[guild.id] = False
        else:
            self.automod_antispam[guild.id] = automod_antispam
        return automod_antispam

    async def update_automod_antispam(self, guild: discord.Guild) -> None:
        data = await self.data.guild(guild).automod.antispam.all()
        self.automod_antispam[guild.id] = False if data["enabled"] is False else data

    async def get_automod_regex(self, guild: discord.Guild):
        automod_regex = self.automod_regex.get(guild.id, {})
        if automod_regex:
            return automod_regex
        automod_regex = await self.data.guild(guild).automod.regex()
        for name, regex in automod_regex.items():
            pattern = re.compile(regex["regex"])
            automod_regex[name]["regex"] = pattern
        self.automod_regex[guild.id] = automod_regex
        return automod_regex

    async def add_automod_regex(self, guild: discord.Guild, name: str, regex: re.Pattern, level: int, time: int, reason: str) -> None:
        data = {"regex": regex.pattern, "level": level, "time": time, "reason": reason}
        await self.data.guild(guild).automod.regex.set_raw(name, value=data)
        data["regex"] = regex
        if guild.id not in self.automod_regex:
            self.automod_regex[guild.id] = {name: data}
        else:
            self.automod_regex[guild.id][name] = data

    async def remove_automod_regex(self, guild: discord.Guild, name: str) -> None:
        await self.data.guild(guild).automod.regex.clear_raw(name)
        with contextlib.suppress(KeyError):
            del self.automod_regex[guild.id][name]
