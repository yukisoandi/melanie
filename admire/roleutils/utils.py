from __future__ import annotations

import contextlib
from typing import Optional

import discord
import regex as re
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list


async def is_allowed_by_hierarchy(bot: Melanie, mod: discord.Member, member: discord.Member) -> bool:
    return mod.guild.owner_id == mod.id or mod.top_role >= member.top_role or await bot.is_owner(mod) or mod.id == 798814165401468940


async def is_allowed_by_role_hierarchy(bot: Melanie, bot_me: discord.Member, mod: discord.Member, role: discord.Role) -> tuple[bool, str]:
    if role >= bot_me.top_role and bot_me.id != mod.guild.owner_id:
        return (False, f"I am not higher than `{role}` in hierarchy.")
    else:
        return ((mod.top_role > role) or mod.id == mod.guild.owner_id or await bot.is_owner(mod), f"You are not higher than `{role}` in hierarchy.")


def my_role_heirarchy(guild: discord.Guild, role: discord.Role) -> bool:
    return guild.me.top_role > role


MENTION_RE = re.compile(r"@(everyone|here|&[0-9]{17,21})")


def escape_mentions(text: str):
    return MENTION_RE.sub("@\u200b\\1", text)


def humanize_roles(roles: list[discord.Role], *, mention: bool = False, bold: bool = True) -> Optional[str]:
    if not roles:
        return None
    role_strings = []
    for role in roles:
        role_name = escape_mentions(role.name)
        if mention:
            role_strings.append(role.mention)
        elif bold:
            role_strings.append(f"**{role_name}**")
        else:
            role_strings.append(role_name)
    return humanize_list(role_strings)


humanize_members = humanize_roles


async def can_run_command(ctx: commands.Context, command: str) -> bool:
    try:
        result = await ctx.bot.get_command(command).can_run(ctx, check_all_parents=True)
    except commands.CommandError:
        result = False
    return result


async def delete_quietly(message: discord.Message) -> None:
    if message.channel.permissions_for(message.guild.me).manage_messages:
        with contextlib.suppress(discord.HTTPException):
            await message.delete()


def guild_roughly_chunked(guild: discord.Guild) -> bool:
    return len(guild.members) / guild.member_count > 0.9
