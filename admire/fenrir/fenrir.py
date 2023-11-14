from __future__ import annotations

import time

import discord
import xxhash
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import log


class Fenrir(commands.Cog):
    """lol."""

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot
        self.kicks: list = []
        self.bans: list = []
        self.mutes: list = []
        self.feedback: dict = {}
        default_guild: dict = {"mute_role": None}

        self.config: Config = Config.get_conf(self, 228492507124596736)
        self.config.register_guild(**default_guild)
        self.notification_targets = {}

    @commands.command(hidden=True)
    @checks.has_permissions(administrator=True)
    @commands.guild_only()
    async def fenrirban(self, ctx: commands.Context) -> None:
        """Create a reaction emoji to ban users."""
        msg = await ctx.send("React to this message to be banned!")
        await self.bot.redis.hset("fenririds", msg.id, time.time())
        self.notification_targets[msg.id] = ctx.channel
        self.bans.append(msg.id)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

    async def is_mod_or_admin(self, member: discord.Member) -> bool:
        guild = member.guild
        if member == guild.owner:
            return True

        if await self.bot.is_owner(member):
            log.warning("Ingoring ban for owner")
            return True
        if await self.bot.is_admin(member):
            return True
        if await self.bot.is_mod(member):
            return True

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:  # sourcery no-metrics
        guild: discord.Guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        if payload.message_id in self.bans:
            notif_channel: discord.TextChannel = self.notification_targets[payload.message_id]
            member: discord.Member = guild.get_member(payload.user_id)
            if member is None:
                return
            if member.bot:
                return
            if member.top_role >= guild.me.top_role:
                return
            if await self.is_mod_or_admin(member):
                return
            if member.premium_since:
                if not await self.bot.redis.ratelimited(f"fenrir_booster:{payload.channel_id}{payload.user_id}", 1, 60):
                    await notif_channel.send(f"You're lucky you're boosting, {member.mention}.")
                return
            if await self.bot.redis.get(f"known_worker:{xxhash.xxh32_hexdigest(str(member.id))}"):
                return log.warning("Ignoring ban for known worker ")
            await member.ban(reason="They asked for it.", delete_message_days=0)
            await notif_channel.send(f"{member.mention} was banned. 😂")
