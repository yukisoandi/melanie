from __future__ import annotations

import asyncio
import contextlib

import discord
from aiomisc.periodic import PeriodicCallback
from cachetools import TTLCache
from discord.http import Route
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from xxhash import xxh32_hexdigest

from melanie import (
    MelanieRedis,
    aiter,
    aiterdict,
    cancel_tasks,
    default_lock_cache,
    make_e,
)
from melanie.stats import MelanieStatsPool
from melanie.vendor.disputils import BotConfirmation

from .helpers import ChannelSettings, GuildSettings, MemberSettings, VoiceRegion


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"


async def last_channel(stats_pool: MelanieStatsPool, member: discord.Member) -> discord.TextChannel:
    res = await stats_pool.submit_query(
        "select last(channel_id,created_at) last_channel_id, channel_name, created_at from guild_messages where user_id = %s and guild_id = %s",
        (str(member.id), str(member.guild.id)),
    )
    if res:
        res = res[0]
    if res and res.last_channel_id:
        return member.guild.get_channel(int(res.last_channel_id))


class VoiceMaster(commands.Cog):
    """VoiceMaster."""

    async def fetch_voice_regions(self) -> None:
        await self.bot.waits_uptime_for(20)
        route = V9Route("GET", "/voice/regions")
        data = await self.bot.http.request(route)
        async for item in aiter(data):
            region = VoiceRegion(**item)
            self.voice_regions[region.id] = region

    def __init__(self, bot: Melanie) -> None:
        self.redis: MelanieRedis = bot.redis
        self.bot = bot
        self.voice_regions: dict[str, VoiceRegion] = {}
        self.closed = False
        self.last_msg_cache = {}
        self.config_cache = TTLCache(50000000, 15)
        self.config = Config.get_conf(self, identifier=813636489074049055, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.config.register_member(**MemberSettings().dict())
        self.config.register_channel(**ChannelSettings().dict())
        self.active_tasks = []
        self.updates_cb = PeriodicCallback(self.updates)
        self.updates_cb.start(15)
        self.regions_cb = PeriodicCallback(self.fetch_voice_regions)
        self.regions_cb.start(120)

        self.locks = default_lock_cache()

    def cog_unload(self) -> None:
        self.closed = True
        cancel_tasks(self.active_tasks)
        self.updates_cb.stop(True)
        self.regions_cb.stop(True)

    async def get_guild_settings(self, guild) -> GuildSettings:
        if guild.id not in self.config_cache:
            self.config_cache[guild.id] = GuildSettings(**(await self.config.guild(guild).all()))
        return self.config_cache[guild.id]

    async def updates(self) -> None:
        await self.bot.waits_uptime_for(30)
        all_channels = await self.config.all_channels()
        async for channel_id, settings in aiterdict(all_channels):
            if settings["channel_owner"]:
                channel: discord.VoiceChannel = self.bot.get_channel(channel_id)
                if channel and not [m for m in channel.members if not m.bot]:
                    with contextlib.suppress(discord.HTTPException, asyncio.TimeoutError):
                        async with asyncio.timeout(2):
                            await channel.delete()
                        log.success("Stale channel removed. {}/{}", channel, channel.guild)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if not self.bot.is_ready() or member.bot or (before.channel and after.channel and before.channel.id == after.channel.id):
            return

        guild: discord.Guild = member.guild
        key = f"vm:create_{member.id}_{guild.id}"
        settings = await self.get_guild_settings(guild)
        join_channel: discord.VoiceChannel = guild.get_channel(settings.join_channel)
        voice_cat: discord.CategoryChannel = guild.get_channel(settings.voice_category)
        if not voice_cat or not join_channel:
            return

        if before.channel and before.channel != join_channel and before.channel.category_id == voice_cat.id:
            lock = self.locks[before.channel.id]
            async with lock:
                before.channel = self.bot.get_channel(before.channel.id)
                if not before.channel:
                    return

                if not [m for m in before.channel.members if not m.bot]:
                    await before.channel.delete()
                    await self.config.channel(before.channel).clear()

        async with self.locks[member.id]:
            if after.channel == join_channel:
                if not guild.get_member(member.id):
                    return
                if await self.redis.ratelimited(key, 5, 90) and member.id not in self.bot.owner_ids:
                    await member.move_to(None, reason="member is ratelimited from creating new vc")

                    return log.warning("Create ratelimit: {} @ {}", member, guild)
                member_conf = MemberSettings(**(await self.config.member(member).all()))
                bitrate: float = guild.bitrate_limit
                channel_name = member_conf.channel_name
                region = member_conf.default_region if member_conf.default_region in self.voice_regions else None
                if not channel_name:
                    channel_name = f"{member.display_name}'s channel".lower()
                try:
                    new_vc: discord.VoiceChannel = await voice_cat.create_voice_channel(
                        channel_name,
                        bitrate=bitrate,
                        rtc_region=region,
                        limit=settings.channel_limit,
                    )
                except discord.HTTPException:
                    hash_name = xxh32_hexdigest(str(member.display_name))[:4]
                    channel_name = f"{hash_name}'s vc"
                    new_vc: discord.VoiceChannel = await voice_cat.create_voice_channel(
                        channel_name,
                        bitrate=guild.bitrate_limit,
                        rtc_region=region,
                        limit=settings.channel_limit,
                    )
                await member.move_to(new_vc, reason=f"{member} created a new voicemaster VC")
                await self.config.channel(new_vc).channel_owner.set(member.id)
                await new_vc.set_permissions(member, connect=True, view_channel=True, send_messages=False)
                await new_vc.set_permissions(guild.default_role, send_messages=False)

            if after.channel:
                after.channel = self.bot.get_channel(after.channel.id)
                channel_settings = await self.get_channel_settings(after.channel)
                if not channel_settings.channel_owner:
                    return
                if member.id in channel_settings.channel_permits:
                    return
                if member.id == channel_settings.channel_owner:
                    return
                if channel_settings.locked_channel:
                    return await member.move_to(None, reason="VoiceMaster force lock.")

                if member.id in channel_settings.channel_rejects:
                    return await member.move_to(None, reason="VoiceMaster force lock.")

    def set_channel_settings(self, channel) -> None:
        with contextlib.suppress(KeyError):
            del self.config_cache[channel.id]

    async def get_channel_settings(self, channel) -> ChannelSettings:
        if channel.id not in self.config_cache:
            self.config_cache[channel.id] = ChannelSettings(**(await self.config.channel(channel).all()))
        return self.config_cache[channel.id]

    async def _create_invite(self, ctx, app_id: int, app_name: str):
        max_age = 86400
        voice = ctx.author.voice
        if not voice:
            return await ctx.send("You have to be in a voice channel to use this command.")
        if voice.channel.permissions_for(ctx.me).create_instant_invite is not True:
            return await ctx.send("I need the `Create Invite` permission for your channel before you can use this command.")

        r = Route("POST", "/channels/{channel_id}/invites", channel_id=voice.channel.id)
        payload = {"max_age": max_age, "target_type": 2, "target_application_id": app_id}
        code = (await self.bot.http.request(r, json=payload))["code"]

        await ctx.send(embed=discord.Embed(description=f"[Click here to join {app_name} in {voice.channel.name}!](https://discord.gg/{code})", color=0x2F3136))

    @commands.guild_only()
    @commands.group(name="voicemaster", aliases=["vm", "voice", "vc", "v"])
    async def vm(self, ctx: commands.Context) -> None:
        """VoiceMaster - Private Channels."""

    @vm.command(name="region")
    async def region(self, ctx: commands.Context, region: str = None):
        """Move the region of the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel

        if not region:
            region_str = "".join(f"{r} \n" for r in self.voice_regions)
            msg = f"Which region would you like to move to? \n \n   {region_str}"

            def check(m):
                return m.author == ctx.author and m.content.lower().strip() in self.voice_regions

            await ctx.reply(embed=make_e(msg, status="info"))
            try:
                response = await self.bot.wait_for("message", check=check, timeout=30)
            except TimeoutError:
                return await ctx.send(embed=make_e("Timeout while selecting a region", status=3))
            region = response.content

        region = region.lower().strip()
        if region not in self.voice_regions:
            return await ctx.reply(embed=make_e(f"{region} is not a valid region to choose from.", status=3))

        await channel.edit(rtc_region=region)
        return await ctx.reply(embed=make_e(f"I've set the region to {region}"))

    @vm.group(name="set")
    async def vm_set(self, ctx: commands.Context) -> None:
        """Set default settings."""

    @vm_set.command(name="region")
    async def vmset_region(self, ctx: commands.Context, region: str = None):
        """Set the default region of your owned channels."""
        if not region:
            region_str = "".join(f"{r} \n" for r in self.voice_regions)
            msg = f"Which region would you like to set as default? \n \n   {region_str}"

            def check(m):
                return m.author == ctx.author and m.content.lower().strip() in self.voice_regions

            await ctx.reply(embed=make_e(msg, status="info"))
            try:
                response = await self.bot.wait_for("message", check=check, timeout=30)
            except TimeoutError:
                return await ctx.send(embed=make_e("Timeout while selecting a region", status=3))
            region = response.content

        region = region.lower().strip()
        if region not in self.voice_regions:
            return await ctx.reply(embed=make_e(f"{region} is not a valid region to choose from.", status=3))

        await self.config.member(ctx.author).default_region.set(region)
        return await ctx.reply(embed=make_e(f"I've set your default region to {region}"))

    @vm.command()
    @checks.has_permissions(administrator=True)
    async def setup(self, ctx: commands.Context):
        """Setup VoiceMaster for your server."""
        guild = ctx.guild
        confirmation = BotConfirmation(ctx, 0x010101)
        settings = GuildSettings(**(await self.config.guild(ctx.guild).all()))

        await confirmation.confirm(
            "I need to create the channel category and the 'Join to Create' channel.",
            description="Is this OK?",
            hide_author=True,
            timeout=30,
        )

        if not confirmation.confirmed:
            return await confirmation.update("Setup canceled.", hide_author=True, color=0xFF5555, description="")

        if settings.voice_category:
            voice_cat: discord.CategoryChannel = guild.get_channel(settings.voice_category)
            if voice_cat:
                await voice_cat.delete()

        if settings.join_channel and (join_channel := guild.get_channel(settings.join_channel)):
            await join_channel.delete()

        voice_cat = await ctx.guild.create_category_channel("Private Channels")

        voice_channel = await ctx.guild.create_voice_channel("Join to Create", category=voice_cat)

        await self.config.guild(ctx.guild).voice_category.set(voice_cat.id)
        await self.config.guild(ctx.guild).join_channel.set(voice_channel.id)

        await confirmation.update(
            "VoiceMaster channel and category created",
            color=0x00F80C,
            description="The channels may appear at the bottom of the channel list. Feel free to rename & reorganize to how you'd like. ",
            hide_author=True,
        )
        self.config_cache = {}

    @commands.cooldown(1, 8, commands.BucketType.user)
    @vm.command()
    async def lock(self, ctx: commands.Context):
        """Lock channel to only permitted users."""
        if not await self.channel_owner_check(ctx):
            return
        async with ctx.typing(), asyncio.timeout(55):
            channel: discord.VoiceChannel = ctx.author.voice.channel
            role = ctx.guild.default_role
            current_perms = channel.overwrites_for(role)
            if current_perms.connect is False:
                return await ctx.reply(embed=make_e("Your voice channel is already locked", status=2))
            if current_perms.view_channel is False:
                await channel.set_permissions(role, view_channel=False, connect=False)
            else:
                await channel.set_permissions(role, connect=False)
            prefixes = await self.bot.get_valid_prefixes(ctx.guild)

            prefixes = [p for p in prefixes if "@" not in p]
            p = prefixes[0]

            async with self.config.channel(channel).all() as _settings:
                settings = ChannelSettings(**_settings)
                settings.locked_channel = True
                for member in channel.members:
                    settings.channel_permits.append(member.id)
                    await channel.set_permissions(member, connect=True)
                _settings.update(settings.dict())
            self.set_channel_settings(channel)

            await ctx.reply(
                embed=make_e(
                    f"{channel.mention} is locked. ",
                    status="lock",
                    tip=f"Use {p}vm permit @member to allow someone to join or {p}vm unlock to allow all.",
                ),
            )

    async def channel_owner_check(self, ctx: commands.Context) -> bool:
        if not ctx.author.voice:
            await ctx.send(embed=make_e("You're not currently in a voice channel.", status=3))
            return False
        channel: discord.VoiceChannel = ctx.author.voice.channel
        if not channel:
            await ctx.send(embed=make_e("You're not currently in a voice channel.", status=3))
            return False
        channel_settings = ChannelSettings(**(await self.config.channel(channel).all()))
        if not channel_settings.channel_owner:
            await ctx.send(embed=make_e("This isn't a VoiceMaster channel.", status=3))
            return False
        if channel_settings.channel_owner != ctx.author.id:
            await ctx.send(embed=make_e(f"You don't own {channel.mention}", status=3))
            return False
        return True

    @commands.cooldown(1, 8, commands.BucketType.user)
    @vm.command()
    async def unlock(self, ctx: commands.Context):
        """Unlock the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel
        role = ctx.guild.default_role

        settings = await self.get_channel_settings(channel)
        if not settings.locked_channel:
            return await ctx.reply(embed=make_e(f"{channel.mention} was already unlocked", status=2))

        async with asyncio.TaskGroup() as tg:
            tg.create_task(channel.set_permissions(role, connect=True))
            tg.create_task(self.config.channel(channel).locked_channel.set(False))
            self.set_channel_settings(channel)
            tg.create_task(ctx.reply(embed=make_e(f"{channel.mention} unlocked", status="unlock")))

    @commands.cooldown(2, 8, commands.BucketType.user)
    @vm.command(aliases=["allow"])
    async def permit(self, ctx: commands.Context, member: discord.Member) -> None:
        """Permit a user to join the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel
        await channel.set_permissions(member, connect=True, view_channel=True)

        async with self.config.channel(channel).all() as _settings:
            settings = ChannelSettings(**_settings)
            if member.id not in settings.channel_permits:
                settings.channel_permits.append(member.id)
            if member.id in settings.channel_rejects:
                settings.channel_rejects.remove(member.id)
            _settings.update(settings.dict())

        em = make_e(f"{member.mention} has been permitted to join {channel.mention}")
        self.set_channel_settings(channel)
        await ctx.reply(embed=em)

    @commands.cooldown(3, 8, commands.BucketType.user)
    @vm.command(aliases=["forbid", "kick", "mute", "remove"])
    async def reject(self, ctx: commands.Context, member: discord.Member) -> None:
        """Reject a user from joining the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel
        await channel.set_permissions(member, connect=False)
        async with self.config.channel(channel).all() as _settings:
            settings = ChannelSettings(**_settings)
            if member.id in settings.channel_permits:
                settings.channel_permits.remove(member.id)
            if member.id not in settings.channel_rejects:
                settings.channel_rejects.append(member.id)
            _settings.update(settings.dict())
        if member.voice and member.voice.channel == channel:
            await member.move_to(None, reason=f"Rejected from vc by {ctx.author}")
        em = make_e(f"Rejected {member.mention} from {channel.mention}")
        self.set_channel_settings(channel)
        await ctx.reply(embed=em)

    @commands.cooldown(3, 8, commands.BucketType.user)
    @vm.command()
    async def limit(self, ctx: commands.Context, limit: int):
        """Limit how many people may join the channel."""
        if limit > 99:
            return await ctx.reply(embed=make_e("Limit must be less than 99", status=3))

        channel: discord.VoiceChannel = ctx.author.voice.channel

        if not await self.channel_owner_check(ctx):
            return

        await channel.edit(user_limit=limit)
        self.set_channel_settings(channel)
        return await ctx.tick()

    @commands.cooldown(1, 8, commands.BucketType.user)
    @vm.command(aliases=["rename"])
    async def name(self, ctx: commands.Context, *, name: str):
        """Set the name of the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel
        key = f"VOICERENAME{channel.id}"
        if await self.redis.ratelimited(key, 3, 600):
            log.warning("RENAME: {} @ {}", ctx.author, ctx.guild)
            return await ctx.reply(embed=make_e("Channels may only re renamed every 10 minutes", status=2))

        try:
            async with asyncio.timeout(10):
                await channel.edit(name=name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return await ctx.send(embed=make_e(f"Unable to set the channel to that name.\n{e}", status=3))

        await self.config.member(ctx.author).channel_name.set(name)
        self.set_channel_settings(channel)

        return await ctx.send(embed=make_e(f"I set your channel name to {name}", tip="All future VC's will be created with this name"))

    @commands.cooldown(1, 8, commands.BucketType.user)
    @vm.command()
    async def claim(self, ctx: commands.Context):
        """Claim ownership of channel once the owner has left."""
        channel = ctx.author.voice.channel
        if not channel:
            return await ctx.reply(embed=make_e("You're not in a voice channel.", status=3))
        voice_owner: int = await self.config.channel(channel).channel_owner()

        if not voice_owner:
            em = make_e(f"You cannot own {channel.mention}", status=3)
            return await ctx.reply(embed=em)

        if voice_owner in [member.id for member in channel.members]:
            owner = ctx.guild.get_member(voice_owner)
            em = make_e(f"{channel.mention} is already owned by {owner.mention}", status=3)
            await ctx.reply(embed=em)

        else:
            if current_owner_member := ctx.guild.get_member(voice_owner):
                await channel.set_permissions(current_owner_member, overwrite=None)
            await channel.set_permissions(ctx.author, connect=True)
            await self.config.channel(channel).channel_owner.set(ctx.author.id)
            self.set_channel_settings(channel)
            return await ctx.reply(embed=make_e(f"You are now the owner of {channel.mention}", status=1))

    @commands.cooldown(1, 8, commands.BucketType.user)
    @vm.command()
    async def owner(self, ctx: commands.Context, channel: discord.VoiceChannel = None):
        """View the owner of your current channel."""
        if not channel:
            if not ctx.author.voice:
                return await ctx.reply(embed=make_e("You're not in a voice channel.", status=3))
            channel = ctx.author.voice.channel

        voice_owner: int = await self.config.channel(channel).channel_owner()

        if not voice_owner:
            em = make_e(f"{channel.mention} is not a VoiceMaster channel.", status=2)
            return await ctx.reply(embed=em)
        owner = ctx.guild.get_member(voice_owner)
        self.set_channel_settings(channel)
        return await ctx.reply(embed=make_e(f"{owner.mention} owns {channel.mention}."))

    @vm.command()
    async def music(self, ctx: commands.Context):
        # sourcery skip: merge-else-if-into-elif, merge-nested-ifs, swap-if-else-branches, swap-nested-ifs
        # sourcery skip: merge-nested-ifs
        """Mute the channel for Music only VC."""
        author: discord.Member = ctx.author
        if not ctx.author.voice:
            return await ctx.send(embed=make_e("This command is to be used when in VC", 3))
        channel: discord.VoiceChannel = ctx.author.voice.channel

        channel_settings = ChannelSettings(**(await self.config.channel(channel).all()))
        if not ctx.bot_owner:
            if not channel_settings.channel_owner:
                if not author.guild_permissions.manage_channels:
                    return await ctx.send(embed=make_e("This command requires manage channels permissions if used with a non Melanie VoiceMaster channel", 3))

            else:
                if channel_settings.channel_owner != ctx.author.id:
                    return await ctx.send(embed=make_e(f"You don't own {channel.mention}", status=3))

        channel: discord.VoiceChannel = ctx.author.voice.channel

        role = ctx.guild.default_role

        current_perms = channel.overwrites_for(role)
        if current_perms.speak is False:
            await channel.set_permissions(role, speak=None)

            return await ctx.reply(embed=make_e(f"{channel.mention} is no longer quiet and speaking is allowed."))

        elif current_perms.connect is False and current_perms.view_channel is False:
            await channel.set_permissions(role, view_channel=False, connect=False, speak=False)
            await ctx.reply(embed=make_e(f"{channel.mention} is now hidden, locked, and quiet", status="music"), delete_after=30)

        elif current_perms.connect is False:
            await channel.set_permissions(role, connect=False, speak=False)
            await ctx.reply(delete_after=30, embed=make_e(f"{channel.mention} is now locked, and quiet", status="music"))

        else:
            await ctx.reply(delete_after=30, embed=make_e(f"{channel.mention} now quiet & music only", status="music"))
            await channel.set_permissions(role, speak=False)
        self.set_channel_settings(channel)

    @vm.command(aliases=["hide"])
    async def ghost(self, ctx: commands.Context):
        """Hide the channel."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel

        role = ctx.guild.default_role

        current_perms = channel.overwrites_for(role)
        self.set_channel_settings(channel)
        if current_perms.view_channel is False:
            return await ctx.reply(embed=make_e(f"{channel.mention} is already hidden."))
        if current_perms.connect is False:
            await ctx.reply(embed=make_e(f"{channel.mention} is now hidden and locked.", status="lock"))
            await channel.set_permissions(role, view_channel=False, connect=False)
        else:
            await ctx.reply(embed=make_e(f"{channel.mention} is hidden."))
            await channel.set_permissions(role, view_channel=False)

    @vm.command(aliases=["show", "unhide"])
    async def unghost(self, ctx: commands.Context):
        """Make the channel visible."""
        if not await self.channel_owner_check(ctx):
            return

        channel: discord.VoiceChannel = ctx.author.voice.channel

        role = ctx.guild.default_role

        current_perms = channel.overwrites_for(role)
        if current_perms.view_channel is True or current_perms.view_channel is None:
            return await ctx.reply(embed=make_e("This voice channel is already visible."))
        self.set_channel_settings(channel)

        await channel.set_permissions(role, view_channel=None)
        await ctx.send(embed=make_e(f"{channel.mention} is now visible."))

    async def get_prefix(self, message: discord.Message) -> str:
        """From melaniebot Alias Cog Tries to determine what prefix is used in a
        message object. Looks to identify from longest prefix to smallest. Will
        raise ValueError if no prefix is found.

        :param message: Message object
        :return:

        """
        try:
            guild = message.guild
        except AttributeError:
            guild = None
        content = message.content
        try:
            prefixes = await self.bot.get_valid_prefixes(guild)
        except AttributeError:
            # Melanie 3.1 support
            prefix_list = await self.bot.command_prefix(self.bot, message)
            prefixes = sorted(prefix_list, key=lambda pfx: len(pfx), reverse=True)
        for p in prefixes:
            if content.startswith(p):
                return p
        msg = "No prefix found."
        raise ValueError(msg)
