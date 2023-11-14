from __future__ import annotations

from typing import Optional, Union

import discord


async def get_audit_log_reason(
    guild: discord.Guild,
    target: Union[discord.abc.GuildChannel, discord.Member, discord.Role],
    action: discord.AuditLogAction,
) -> tuple[Optional[discord.abc.User], Optional[str]]:
    perp = None
    reason = None
    if guild.me.guild_permissions.view_audit_log:
        async for log in guild.audit_logs(limit=5, action=action):
            if log.target.id == target.id:
                perp = log.user
                if log.reason:
                    reason = log.reason
                break
    return perp, reason
