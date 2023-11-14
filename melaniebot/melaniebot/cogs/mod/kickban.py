from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any, Optional, Union

import discord
import distributed
from boltons.iterutils import chunked_iter
from loguru import logger as log
from melanie import make_e, yesno

from melaniebot.core import checks, commands, modlog
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import bold, format_perms_list
from melaniebot.core.utils.mod import get_audit_reason

from .abc import MixinMeta  # type: ignore
from .utils import is_allowed_by_hierarchy


def _(x):
    return x


async def cancel_futures(client: distributed.Client, futures: list[distributed.Future]):
    for future in futures:
        if not future.done():
            await client.cancel(future)
        else:
            future.release()


def ban_to_dict(b):
    return {"user_id": b.user.id, "name": b.user.display_name, "username": str(b.user), "reason": b.reason or None}


class KickBanMixin(MixinMeta):
    locks: dict[int, asyncio.Lock]
    """Kick and ban commands and tasks go here."""

    @staticmethod
    async def get_invite_for_reinvite(ctx: commands.Context, max_age: int = 86400):
        """Handles the reinvite logic for getting an invite
        to send the newly unbanned user
        :returns: :class:`Invite`.
        """
        guild = ctx.guild
        my_perms: discord.Permissions = guild.me.guild_permissions
        if my_perms.manage_guild or my_perms.administrator:
            if "VANITY_URL" in guild.features:
                # guild has a vanity url so use it as the one to send
                with contextlib.suppress(discord.NotFound):
                    return await guild.vanity_invite()
            invites = await guild.invites()
        else:
            invites = []
        for inv in invites:
            if not (inv.max_uses or inv.max_age or inv.temporary):
                # Invite is for the guild's default channel,
                # has unlimited uses, doesn't expire, and
                # doesn't grant temporary membership
                # (i.e. they won't be kicked on disconnect)
                return inv
        channels_and_perms = zip(guild.text_channels, map(guild.me.permissions_in, guild.text_channels))
        channel = next((channel for channel, perms in channels_and_perms if perms.create_instant_invite), None)
        if channel is None:
            return
        try:
            # Create invite that expires after max_age
            return await channel.create_invite(max_age=max_age)
        except discord.HTTPException:
            return

    @staticmethod
    async def _voice_perm_check(ctx: commands.Context, user_voice_state: Optional[discord.VoiceState], **perms: bool) -> bool:
        """Check if the bot and user have sufficient permissions for voicebans.

        This also verifies that the user's voice state and connected
        channel are not ``None``.

        Returns
        -------
        bool
            ``True`` if the permissions are sufficient and the user has
            a valid voice state.

        """
        if user_voice_state is None or user_voice_state.channel is None:
            await ctx.send(embed=make_e("That user is not in a voice channel.", status=2))
            return False
        voice_channel: discord.VoiceChannel = user_voice_state.channel
        required_perms = discord.Permissions()
        required_perms.update(**perms)

        if not voice_channel.permissions_for(ctx.me) >= required_perms:
            await ctx.send(embed=make_e(f"I require the {format_perms_list(required_perms)} permission(s) in that user's channel to do that."))
            return False
        if ctx.permission_state is commands.PermState.NORMAL and not voice_channel.permissions_for(ctx.author) >= required_perms:
            await ctx.send(embed=make_e(f"You must have the {format_perms_list(required_perms)} permission(s) in that user's channel to use this command."))
            return False
        return True

    async def ban_user(
        self,
        user: Union[discord.Member, discord.User, discord.Object],
        ctx: commands.Context,
        days: int = 0,
        reason: str = None,
        create_modlog_case=False,
    ) -> tuple[bool, str]:
        author = ctx.author
        guild = ctx.guild

        removed_temp = False

        if not (0 <= days <= 7):
            return False, "Invalid days. Must be between 0 and 7."

        if isinstance(user, discord.Member):
            if author == user:
                return (False, ("I cannot let you do that. Self-harm is bad {}").format("\N{PENSIVE FACE}"))
            elif not await is_allowed_by_hierarchy(self.bot, self.config, guild, author, user):
                return (False, "I cannot let you do that. You are not higher than the user in the role hierarchy.")
            elif guild.me.top_role <= user.top_role or user == guild.owner:
                return False, "I cannot do that due to Discord hierarchy rules."

            toggle = await self.config.guild(guild).dm_on_kickban()
            if toggle:
                with contextlib.suppress(discord.HTTPException):
                    em = discord.Embed(title=bold(f"You have been banned from {guild}."), color=await self.bot.get_embed_color(user))
                    em.add_field(name="**Reason**", value=reason if reason is not None else ("No reason was given."), inline=False)
                    await user.send(embed=em)

            ban_type = "ban"
        else:
            tempbans = await self.config.guild(guild).current_tempbans()

            ban_list = [ban.user.id for ban in await guild.bans()]
            if user.id in ban_list:
                if user.id not in tempbans:
                    return (False, f"User with ID {user.id} is already banned.")

                async with self.config.guild(guild).current_tempbans() as tempbans:
                    tempbans.remove(user.id)
                removed_temp = True
            ban_type = "hackban"

        audit_reason = get_audit_reason(author, reason, shorten=True)

        if removed_temp:
            log.info(f"{author.name}({author.id}) upgraded the tempban for {user.id} to a permaban.")

            success_message = f"User with ID {user.id} was upgraded from a temporary to a permanent ban."
        else:
            username = user.name if hasattr(user, "name") else "Unknown"
            try:
                await guild.ban(user, reason=audit_reason, delete_message_days=days)
                log.info(f"{author.name}({author.id}) {ban_type}ned {username}({user.id}), deleting {days} days worth of messages.")

                success_message = "Done. That felt good."
            except discord.Forbidden:
                return False, "I'm not allowed to do that."
            except discord.NotFound:
                return False, f"User with ID {user.id} not found"
            except Exception:
                log.exception(f"{author.name}({author.id}) attempted to {ban_type} {username}({user.id}), but an error occurred.")

                return False, "An unexpected error occurred."

        if create_modlog_case:
            await modlog.create_case(
                self.bot,
                guild,
                ctx.message.created_at.replace(tzinfo=timezone.utc),
                ban_type,
                user,
                author,
                reason,
                until=None,
                channel=None,
            )

        return True, success_message

    async def tempban_expirations_task(self) -> None:
        while True:
            try:
                await self._check_tempban_expirations()
            except Exception:
                log.exception("Something went wrong in check_tempban_expirations:")

            await asyncio.sleep(60)

    async def _check_tempban_expirations(self) -> None:
        guilds_data = await self.config.all_guilds()
        async for guild_id, guild_data in AsyncIter(guilds_data.items(), steps=20):
            if not (guild := self.bot.get_guild(guild_id)):
                continue
            if guild.unavailable or not guild.me.guild_permissions.ban_members:
                continue
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue

            guild_tempbans = guild_data["current_tempbans"]
            if not guild_tempbans:
                continue
            async with self.config.guild(guild).current_tempbans.get_lock():
                if await self._check_guild_tempban_expirations(guild, guild_tempbans):
                    await self.config.guild(guild).current_tempbans.set(guild_tempbans)

    async def _check_guild_tempban_expirations(self, guild: discord.Guild, guild_tempbans: list[int]) -> bool:
        changed = False
        for uid in guild_tempbans.copy():
            unban_time = datetime.fromtimestamp(await self.config.member_from_ids(guild.id, uid).banned_until(), timezone.utc)
            if datetime.utcnow() > unban_time:
                try:
                    await guild.unban(discord.Object(id=uid), reason="Tempban finished")
                except discord.NotFound:
                    # user is not banned anymore
                    guild_tempbans.remove(uid)
                    changed = True
                except discord.HTTPException as e:
                    # 50013: Missing permissions error code or 403: Forbidden status
                    if e.code == 50013 or e.status == 403:
                        log.info(f"Failed to unban ({uid}) user from {guild.name}({guild.id}) guild due to permissions.")
                        break  # skip the rest of this guild
                    log.info(f"Failed to unban member: error code: {e.code}")
                else:
                    # user unbanned successfully
                    guild_tempbans.remove(uid)
                    changed = True
        return changed

    @commands.command()
    @commands.guild_only()
    @commands.mod_or_permissions(move_members=True)
    async def voicekick(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """Kick a member from a voice channel."""
        author = ctx.author
        guild = ctx.guild
        user_voice_state: discord.VoiceState = member.voice
        if not await self.bot.is_owner(ctx.author):
            if await self._voice_perm_check(ctx, user_voice_state, move_members=True) is False:
                return
            elif not await is_allowed_by_hierarchy(self.bot, self.config, guild, author, member):
                await ctx.send(embed=make_e("I cannot let you do that. You are not higher than the user in the role hierarchy.", status=3))
                return
        case_channel = member.voice.channel
        # Store this channel for the case channel.

        try:
            await member.move_to(None)
        except discord.Forbidden:  # Very unlikely that this will ever occur
            await ctx.send(embed=make_e("I am unable to kick this member from the voice channel.", status=3))
            return
        except discord.HTTPException:
            await ctx.send(embed=make_e("Something went wrong while attempting to kick that member.", status=3))
            return
        else:
            await modlog.create_case(
                self.bot,
                guild,
                ctx.message.created_at.replace(tzinfo=timezone.utc),
                "vkick",
                member,
                author,
                reason,
                until=None,
                channel=case_channel,
            )

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(mute_members=True, deafen_members=True)
    async def voiceunban(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """Unban a user from speaking and listening in the server's voice
        channels.
        """
        user_voice_state = member.voice
        if not await self.bot.is_owner(ctx.author) and not await self._voice_perm_check(ctx, user_voice_state, deafen_members=True, mute_members=True):
            return
        needs_unmute = bool(user_voice_state.mute)
        needs_undeafen = bool(user_voice_state.deaf)
        audit_reason = get_audit_reason(ctx.author, reason, shorten=True)
        if needs_unmute and needs_undeafen:
            await member.edit(mute=False, deafen=False, reason=audit_reason)
        elif needs_unmute:
            await member.edit(mute=False, reason=audit_reason)
        elif needs_undeafen:
            await member.edit(deafen=False, reason=audit_reason)
        else:
            await ctx.send(embed=make_e("That user isn't muted or deafened by the server.", status=2))
            return

        guild = ctx.guild
        author = ctx.author
        await modlog.create_case(
            self.bot,
            guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "voiceunban",
            member,
            author,
            reason,
            until=None,
            channel=None,
        )
        await ctx.send(embed=make_e("User is now allowed to speak and listen in voice channels."))

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(mute_members=True, deafen_members=True)
    async def voiceban(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """Ban a user from speaking and listening in the server's voice channels."""
        user_voice_state: discord.VoiceState = member.voice
        if not await self.bot.is_owner(ctx.author) and not await self._voice_perm_check(ctx, user_voice_state, deafen_members=True, mute_members=True):
            return
        needs_mute = user_voice_state.mute is False
        needs_deafen = user_voice_state.deaf is False
        audit_reason = get_audit_reason(ctx.author, reason, shorten=True)
        author = ctx.author
        guild = ctx.guild
        if needs_mute and needs_deafen:
            await member.edit(mute=True, deafen=True, reason=audit_reason)
        elif needs_mute:
            await member.edit(mute=True, reason=audit_reason)
        elif needs_deafen:
            await member.edit(deafen=True, reason=audit_reason)
        else:
            await ctx.send(embed=make_e("That user is already muted and deafened server-wide.", status=2))
            return

        await modlog.create_case(
            self.bot,
            guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "voiceban",
            member,
            author,
            reason,
            until=None,
            channel=None,
        )
        await ctx.send(embed=make_e("User has been banned from speaking and listening in voice channels."))

    @staticmethod
    async def find_ban(bot, banlist: list, username: str):
        def _clean_and_find(banlist: list, username: str) -> dict:
            from unidecode import unidecode

            username = unidecode(username)
            username = " ".join(username.split()).lower()

            for item in banlist:
                target: str = item["username"] if "#" in username else item["name"]
                target = " ".join(target.split())
                target = unidecode(target)

                if target == username:
                    return item

        futures = []

        for ban_chunk in chunked_iter(banlist, 1000):
            await asyncio.sleep(0)
            job_chunk = [ban_to_dict(b) for b in ban_chunk]
            futures.append(bot.to_cluster(_clean_and_find, job_chunk, username))
        for future in asyncio.as_completed(futures):
            result = await future
            if result:
                [t.cancel() for t in futures]
                return [result]

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def banned(self, ctx: commands.Context, *, unban_input: Union[int, str]):
        """Check to see if a user is currently banned."""
        async with asyncio.timeout(90):
            async with ctx.typing():
                from modsystem.iterators import BanIterator

                bans = []
                async for b in BanIterator(ctx, ctx.guild):
                    bans.append(b)

                if isinstance(unban_input, int) and len(str(unban_input)) < 18:
                    unban_input = str(unban_input)
                exit_method = ctx.send(
                    embed=make_e(f"Unable to find a ban for **{unban_input}** ", tip="Try providing a user id or full username + discriminator", status=2),
                )
                if isinstance(unban_input, str):
                    async with asyncio.timeout(30):
                        banned_user = await self.find_ban(self.bot, bans, unban_input)
                    if not banned_user:
                        return await exit_method
                    banned_user = banned_user[0]

                else:
                    banned_user = [x for x in bans if x.user.id == unban_input]

                    if not banned_user:
                        return await exit_method
                    b = banned_user[0]
                    banned_user = ban_to_dict(b)

                user_id = banned_user["user_id"]
                user_name = banned_user["username"]
                ban_reason = banned_user["reason"]

                ban_reason = f"\n\nReason: {ban_reason}" if ban_reason else ""

                embed = make_e(f"**{user_name} ({user_id})** is currently banned.{ban_reason}", status="info")
                try:
                    async with asyncio.timeout(2):
                        cached_user: discord.User = await self.bot.get_or_fetch_user(user_id)
                except asyncio.TimeoutError:
                    cached_user = None
                if cached_user:
                    embed.set_thumbnail(url=str(cached_user.avatar_url))
                return await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, *, unban_input: Union[int, str]):
        """Unban a user from this server.

        Provide a user id or username. If you wish to unban everyone
        from the server, provide "all" as the input

        """
        async with ctx.typing():
            guild: discord.Guild = ctx.guild
            audit_reason = get_audit_reason(ctx.author, None, shorten=True)
            from modsystem.iterators import BanEntry, BanIterator
            from warden.warden import Warden

            warden: Warden = self.bot.get_cog("Warden")
            hardbans: dict[int, dict[Any, Any]] = {}
            if warden:
                hardbans = await warden.config.guild(ctx.guild).hardbans_set()
                hardbans = {int(k): v for k, v in hardbans.items()}

            bans: list[BanEntry] = []
            async for b in BanIterator(ctx, ctx.guild):
                bans.append(b)

            if unban_input == "all":
                lock = self.locks[ctx.guild.id]

                if lock.locked():
                    return await ctx.send(embed=make_e("Running an unban task for for this server already.", 2))
                ban_count = len(bans)
                async with lock:
                    confirmed, _msg = await yesno(
                        f"There are {ban_count} banned user(s). Would you like to unban them all?",
                        "Users hardbanned by the server owner will be excluded",
                    )
                    if not confirmed:
                        return
                    tracker: discord.Message = await ctx.send(embed=make_e(f"Unbanned 0/{ban_count} users"))
                    errors = 0
                    done = 0
                    for b in bans:
                        if b.user.id in hardbans:
                            continue
                        try:
                            await guild.unban(b.user)
                            done += 1
                            if not await self.bot.redis.ratelimited(f"unbantracker{ctx.guild.id}", 1, 2):
                                try:
                                    await tracker.edit(embed=make_e(f"Unbanned {done}/{ban_count} users"))
                                except discord.NotFound:
                                    return await ctx.send(
                                        embed=make_e("Bailing on the mass unban because my tracker message was deleted. Try again to unban everyone", 3),
                                    )

                        except discord.HTTPException:
                            errors += 1
                            if errors > 10:
                                return await ctx.send(embed=make_e("Bailing due to excessive errors on the mass unban", 3))

                    await tracker.delete(delay=0.01)
                    return await ctx.send(embed=make_e(f"Unbanned {ban_count} users"))

            if isinstance(unban_input, int) and len(str(unban_input)) < 18:
                unban_input = str(unban_input)

            exit_method = ctx.send(
                embed=make_e(f"Unable to find a ban for **{unban_input}** ", tip="Try providing a user id or full username + discriminator", status=2),
            )

            if isinstance(unban_input, str):
                async with asyncio.timeout(30):
                    banned_user = await self.find_ban(self.bot, bans, unban_input)
                if not banned_user:
                    return await exit_method
                banned_user = banned_user[0]

            else:
                banned_user = [x for x in bans if x.user.id == unban_input]

                if not banned_user:
                    return await exit_method
                b = banned_user[0]
                banned_user = ban_to_dict(b)

            user_id = banned_user["user_id"]
            user_name = banned_user["username"]
            ban_reason = banned_user["reason"]

            if banned_user["user_id"] in hardbans:
                return await ctx.send(
                    embed=make_e(
                        f"There is a **hardban** enforced on **{ banned_user['username']}**. \n The hardban was created by **{hardbans[banned_user['user_id']]['ban_author']['name']}**",
                        3,
                        tip="the server owner must remove the hardban.",
                    ),
                )

            try:
                await guild.unban(discord.Object(banned_user["user_id"]), reason=audit_reason)
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(f"Discord gave an error when attempting to unban that user. Error: {e}", status=3))
        ok_msg = f"Unbanned **{user_name}** from the server.\n\n"

        if ban_reason:
            ok_msg += f" **Original ban reason:** {ban_reason}"

        msg = await ctx.send(embed=make_e(ok_msg))
        cached_user: discord.User = self.bot.get_user(user_id)
        if not cached_user or not cached_user.mutual_guilds:
            return
        with contextlib.suppress(TypeError, discord.HTTPException):
            invite = await self.get_invite_for_reinvite(ctx)
            if invite and invite.url and (not await self.bot.redis.ratelimited(f"unbaninvite:{user_id}", 3, 300)):
                await cached_user.send(
                    f"You've been unbanned from {guild.name} by {ctx.author}!\n Here is an invite for that server: {invite.url}.",
                )
                await msg.edit(embed=make_e(ok_msg, tip=f"i was able to send an invite back to {cached_user}"))
