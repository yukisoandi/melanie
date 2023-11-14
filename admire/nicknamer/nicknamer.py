from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from aiomisc import PeriodicCallback
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import AsyncIter, BaseModel, cancel_tasks, capturetime, make_e, spawn_task
from melanie.vendor.disputils import BotConfirmation

if TYPE_CHECKING:
    from nickworker.nickworker import NickNamerWorker


class GuildSettings(BaseModel):
    frozen: list = []
    monitor_nicks: bool = False


class NickNamer(commands.Cog):
    """NickNamer."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=190420201535, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.active_tasks = []
        self.owner_locked = []
        self.self_freeze_db = PeriodicCallback(self.init_self_frozen)
        self.self_freeze_db.start(7200)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)
        self.self_freeze_db.stop(True)

    async def init_self_frozen(self):
        await self.bot.waits_uptime_for(30)
        with capturetime("self freeze cache"):
            for guild in self.bot.guilds:
                async with self.config.guild(guild).frozen() as frozen:
                    frozen: list
                    frozen_entry = await AsyncIter(frozen).find(lambda x: self.bot.user.id in x, default=None)
                    if frozen_entry and frozen_entry[1] != "melanie":
                        frozen.remove(frozen_entry)
                        frozen_entry = None
                    if not frozen_entry:
                        frozen.append([self.bot.user.id, "melanie"])

                await self.reset_cache(guild)

    async def reset_cache(self, guild: discord.Guild) -> None:
        worker: NickNamerWorker = self.bot.get_cog("NickNamerWorker")
        await worker.refresh_config(guild)

    @commands.command()
    @commands.cooldown(5, 90, commands.BucketType.member)
    async def nick(self, ctx: commands.Context, user: Optional[discord.Member], *, nickname: Optional[str]):
        """Forcibly change a user's nickname."""
        nickworker: NickNamerWorker = self.bot.get_cog("NickNamerWorker")

        if user:
            if user.id in self.bot.owner_ids and ctx.author.id not in self.bot.owner_ids:
                return await ctx.message.add_reaction("🤨")
            author: discord.Member = ctx.author
            if ctx.author.id not in self.bot.owner_ids:
                if not author.guild_permissions.manage_nicknames:
                    return await ctx.send(embed=make_e("You must have manage nickname permission before you can edit other people's nicknames", 2))

                if user.top_role > author.top_role:
                    return await ctx.send(embed=make_e(f"{user}'s top role **{user.top_role}** is higher than your top role. ", 2))

        if not user:
            user = ctx.author
            if user.top_role > ctx.guild.me.top_role:
                return await ctx.send(
                    embed=make_e(
                        f"My top role {ctx.guild.me.top_role.mention} is lower than {user.top_role.mention}. Move me higher to edit their nickname",
                        2,
                    ),
                )

            if not nickname:
                if not user.guild_permissions.change_nickname and ctx.author.id not in self.bot.owner_ids:
                    return await ctx.send(embed=make_e("You don't have perms to change your nickname 🤨"))
                if nickworker and user.id in nickworker.frozen_cache[ctx.guild.id]:
                    return await ctx.send(embed=make_e(f"**{user}'s** nickname is currently frozen..", 3))
                if user.nick:
                    await user.edit(nick=None)
                return await ctx.send(embed=make_e("Your nickname has been reset"))

        if ctx.author.top_role < user.top_role and ctx.author.id not in self.bot.owner_ids:
            return await ctx.send(embed=make_e("You can only rename users below your top role", status=3))
        if nickname and len(nickname) >= 32:
            return await ctx.send(embed=make_e("That nickname is too long. Keep it under 32 characters, please. 😡", status=3))
        if ctx.guild.me.top_role < user.top_role:
            return await ctx.send(
                embed=make_e(
                    f"Missing permissions. My top role ({ctx.guild.me.top_role.mention}) must be above {user.mention}'s top role ({user.top_role.mention})",
                    status=3,
                ),
            )

        try:
            if nickworker and user.id in nickworker.frozen_cache[ctx.guild.id]:
                return await ctx.send(embed=make_e(f"**{user}'s** nickname is currently frozen..", 3))
            await asyncio.wait_for(user.edit(nick=nickname), timeout=10)
            await ctx.tick()

        except discord.errors.Forbidden:
            await ctx.send(embed=make_e("I'm missing required server permissions.", status=3))

    async def clean_all_member(self, ctx: commands.Context):
        changed = []
        worker: NickNamerWorker = self.bot.get_cog("NickNamerWorker")
        await worker.refresh_config(ctx.guild)
        checked = 0
        total = len(ctx.guild.members)
        tracker = await ctx.send(embed=make_e(f"Checked: {checked}/{total}\n\nCleaned: {len(changed)}", "info"))

        for member in ctx.guild.members:
            checked += 1
            await asyncio.sleep(0)
            before_name = str(member.display_name)
            await worker.do_nick_clean_or_lock(member)
            await asyncio.sleep(0)
            if member.display_name != before_name:
                changed.append(member)

            if not await self.bot.redis.ratelimited(f"cleantracker:{ctx.guild.id}", 1, 2) and tracker:
                try:
                    await tracker.edit(embed=make_e(f"Checked: {checked}/{total}\n\nCleaned: {len(changed)}", "info"))
                except discord.HTTPException:
                    tracker = None
        if tracker:
            return await tracker.edit(embed=make_e(f"Done! I cleaned {len(changed)} users."))

    @commands.command()
    @checks.has_permissions(manage_nicknames=True)
    async def cleanservernicks(self, ctx: commands.Context):
        """Clean all non standard characters from member's within the guild."""
        await ctx.send(
            embed=make_e(
                "Attempting to transliterate all non standard characters in user's nicknames within the server. This may take a while.",
                status="info",
            ),
        )

        if not await self.config.guild(ctx.guild).monitor_nicks():
            return await ctx.send(embed=make_e("`;monitornicknames` must be first enabled before this can be ran.", 2))

        spawn_task(self.clean_all_member(ctx), self.active_tasks)

    @checks.has_permissions(manage_nicknames=True)
    @commands.command(aliases=["forcenick", "fn"])
    async def locknick(self, ctx: commands.Context, user: discord.Member, *, nickname: Optional[str]):
        """Freeze a users nickname."""
        if user.id == self.bot.user.id:
            return await ctx.send(embed=make_e("I like my name!", 3))
        if user.id in self.bot.owner_ids and ctx.author.id not in self.bot.owner_ids:
            log.warning(f"refusing to locknick on owner from {ctx.author}  / {ctx.guild}")
            return await ctx.message.add_reaction("🤨")

        if ctx.guild.me.top_role < user.top_role:
            return await ctx.send(
                embed=make_e(
                    f"Missing permissions. My top role ({ctx.guild.me.top_role.mention}) must be above {user.mention}'s top role ({user.top_role.mention})",
                    status=3,
                ),
            )

        if ctx.author.top_role <= user.top_role and not await self.bot.is_owner(ctx.author):
            return await ctx.send(embed=make_e("You may only lock the nickname of members whos top role is below yours in the server", status=3))

        try:
            async with self.config.guild(ctx.guild).frozen() as frozen_settings:
                locked = False
                for e in frozen_settings:
                    if user.id in e:
                        frozen_settings.remove(e)
                        locked = True

                if not nickname and not locked:
                    return await ctx.send(embed=make_e(f"**{user}**'s nickname is not locked"))
                if not nickname:
                    return await ctx.send(embed=make_e(f"**{user}**'s nickname has been unlocked"))
                frozen_settings.append((user.id, nickname))
            await self.reset_cache(ctx.guild)
            await user.edit(nick=nickname)
            if ctx.author.id in self.bot.owner_ids:
                self.owner_locked.append(user.id)
            await ctx.tick()

        except discord.errors.HTTPException as e:
            await ctx.send(embed=make_e(f"Error from discord: {e}", status=3))

    @checks.has_permissions(manage_nicknames=True)
    @commands.command(aliases=["unforcenick"])
    async def unlocknick(self, ctx: commands.Context, user: discord.Member):
        """Unfreeze a user's nickname."""
        if user.id == self.bot.user.id:
            return await ctx.send(embed=make_e("I like my name!", 3))
        if user.id in self.owner_locked and ctx.author.id not in self.bot.owner_ids:
            return
        if user.id in self.bot.owner_ids and ctx.author.id not in self.bot.owner_ids:
            return await ctx.message.add_reaction("🤨")

        if ctx.author.top_role <= user.top_role and not await self.bot.is_owner(ctx.author):
            return await ctx.send(embed=make_e("You may only lock the nickname of members whos top role is below yours in the server", status=3))

        async with self.config.guild(ctx.guild).frozen() as frozen:
            unlocked = False

            for e in frozen:
                if user.id in e:
                    frozen.remove(e)
                    unlocked = True
            if not unlocked:
                return await ctx.send(embed=make_e(f"{user.mention} nick is not locked."))

        await self.reset_cache(ctx.guild)
        await ctx.tick()

    @checks.has_permissions(manage_nicknames=True)
    @commands.command()
    async def monitornicknames(self, ctx) -> None:
        """This enables or disables Melanie's lookout for non-standard fonts in
        user's nicknames and immediately reformats them.
        """
        confirmation = BotConfirmation(ctx, 0x012345)
        state = await self.config.guild(ctx.guild).monitor_nicks()
        if state:
            msg = "I'm currently monitoring nicknames. Are you sure you'd like me to stop?"
            new_state = False

        else:
            msg = "This will enable automatic renaming of users who have non standard characters. Are you **sure** you want to enable this?"
            new_state = True

        await confirmation.confirm(msg, description="")
        if confirmation.confirmed:
            await self.config.guild(ctx.guild).monitor_nicks.set(new_state)
            await confirmation.update("Setting updated.", color=0x55FF55)
        else:
            await confirmation.update("No change made.", hide_author=True, color=0xFF5555)
        await self.reset_cache(ctx.guild)
