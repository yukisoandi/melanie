from __future__ import annotations

import discord
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import make_e
from melanie.vendor.disputils import BotConfirmation


class Vanity(commands.Cog):
    """For level 3 servers, award your users for advertising the vanity in their
    status.
    """

    def __init__(self, bot) -> None:
        from vanityworker.vanityworker import GuildSettings, MemberSettings

        self.bot: Melanie = bot
        self.config = Config.get_conf(self, 44342312391260, force_registration=True)
        self.config.register_guild(**GuildSettings().dict())
        self.config.register_member(**MemberSettings().dict())
        self.config_cache = {}
        self.alert_cache = {}

    async def reset_cache(self, guild: discord.Guild) -> None:
        worker = self.bot.get_cog("VanityWorker")
        await worker.refresh_config(guild)

    @commands.guild_only()
    @commands.group(name="vanity")
    @checks.has_permissions(administrator=True)
    async def vanity(self, ctx) -> None:
        """Level 3 servers: Award users for advertising the vanity in their
        status.
        """

    @vanity.command(name="text")
    async def set_vanity_text(self, ctx: commands.Context, vanity_str: str):
        """Set the text that should be searched in the user's status.

        ie 'gg/vanity'

        """
        guild = ctx.message.guild
        if guild.premium_tier != 3:
            return await ctx.send("This feature is reserved or level 3 servers.")
        await self.config.guild(guild).vanityString.set(vanity_str)
        await ctx.send(f"I've set the vanity string {vanity_str}")
        await self.reset_cache(ctx.guild)

    @vanity.command(name="enable")
    async def enable_vanity(self, ctx):
        """Enable vanity watching on this server."""
        if ctx.guild.premium_tier != 3:
            return await ctx.send("This feature is reserved or level 3 servers.")
        state = await self.config.guild(ctx.guild).enabled()
        if state:
            await ctx.send("Vanity watching disabled.")
            await self.config.guild(ctx.guild).enabled.set(False)
        else:
            await ctx.send("Enabled vanity watching on this server.")
            await self.config.guild(ctx.guild).enabled.set(True)
        await self.reset_cache(ctx.guild)

    @vanity.command(name="role", aliases=["award"])
    async def _set_award_role(self, ctx: commands.Context, role: discord.Role = None) -> None:
        """Configure or view the role set to users when they advertise the vanity."""
        if not role:
            role_set = await self.config.guild(ctx.guild).awardedRole()
            if role_set is None:
                await ctx.send(
                    "No role is set. Run this command with the role provided. **You don't need to mention.** I know how to convert role names or IDs.",
                )

            if role_set is not None:
                r = ctx.guild.get_role(role_set)
                await ctx.send(f"I'm currently assigning {r.name} | {r.id}. Run this command again with the role provided to change it.")
        else:
            bot_perms = ctx.guild.me.guild_permissions
            if not bot_perms.manage_roles:
                await ctx.send("I'm missing the Managed Roles permissions. Please grant this to me and try setting the role again. ")
                return
            bot_top_role = ctx.guild.me.top_role
            if role.position > bot_top_role.position:
                await ctx.send("That role is above me. I need to be above that role in order to give it to users.")
                return

            await self.config.guild(ctx.guild).awardedRole.set(role.id)
            await ctx.send(f"{role.name} has been added.")
        await self.reset_cache(ctx.guild)

    @vanity.command(name="blacklist", aliases=["ignore"])
    async def _ignore_user(self, ctx: commands.Context, member: discord.Member = None):
        """Add or remove user from the vanity blacklisted.

        Users added won't be given the awarded role and will be ignored
        by this feature.

        """
        if not member:
            await ctx.send("I'll need a user to ignore or un-ignore. Run this command again with a given server member.")

        if member:
            blacklist = await self.config.guild(ctx.guild).blacklist()
            if member.id in blacklist:
                blacklist.remove(member.id)
                await self.config.guild(ctx.guild).blacklist.set(blacklist)
                await ctx.send("This user is currently in the blacklist. I've removed them.")
                return await self.reset_cache(ctx.guild)
            blacklist.append(member.id)
            await self.config.guild(ctx.guild).blacklist.set(blacklist)
            await ctx.send("Added that user to the blacklist. ")
            await self.reset_cache(ctx.guild)

    @vanity.command(name="number")
    async def _set_num_msg_required(self, ctx: commands.Context, number_messages: int) -> None:
        """Set the number of messages required to be sent before giving the user
        the vanity role.
        """
        await self.config.guild(ctx.guild).num_msg_before_award.set(number_messages)
        await self.reset_cache(ctx.guild)
        await ctx.send(embed=make_e(f"I'll award the user only after they've sent {number_messages} messages"))

    @vanity.command(name="channel")
    async def _set_notif_channel(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set the channel where you want the notifications to go."""
        if not channel:
            await self.config.guild(ctx.guild).notificationChannel.set(None)
            await ctx.send("channel has been deleted.")
        else:
            await self.config.guild(ctx.guild).notificationChannel.set(channel.id)
            await ctx.send(f"{channel.name} has been added.")
            await self.reset_cache(ctx.guild)

    @vanity.command(name="setup")
    async def vanity_setup(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Automated setup of the vanity watcher."""
        if "VANITY_URL" not in ctx.guild.features:
            return await ctx.send(embed=make_e("This guild does not currently have a vanity URL. This feature is for level 3 vanity servers only. ", status=3))
        if not channel:
            return await ctx.send(
                embed=make_e(
                    "I need to know which channel to send the award notification to. This should be your main chat channel. Please re-run this command and provide either the name or channel ID of this notification channel.",
                    status=3,
                ),
            )

        confirmation = BotConfirmation(ctx, 0x010101)
        msg = "The vanity feature requires a new role only for use by me. You cannot add members to this role manually. I'm going to create this role and configure all of the settings necessary. "
        await confirmation.confirm(msg, description="Is this OK?", hide_author=True, timeout=30)
        if not confirmation.confirmed:
            return await confirmation.update("Setup canceled.", hide_author=True, color=0xFF5555, description="")

        role = await ctx.guild.create_role(name="Vanity", hoist=True, mentionable=False, reason="Automated Vanity setup.")

        vanity = await ctx.guild.vanity_invite()
        vanity = vanity.url.replace("discord.gg", "")
        vanity = vanity.replace("https://", "")

        guild_settings_dict = {"vanityString": vanity, "awardedRole": role.id, "notificationChannel": channel.id, "enabled": True, "blacklist": []}

        log.success("Automated vanity setup OK for guild {} ({}) Settings dict: {}", ctx.guild, ctx.guild.id, guild_settings_dict)

        for k, v in guild_settings_dict.items():
            await self.config.guild(ctx.guild).set_raw(k, value=v)

        await self.reset_cache(ctx.guild)

        return await confirmation.update(
            "Vanity watching setup finished!",
            color=0x00F80C,
            description=f"Instruct users to place the text {vanity} in their status to be awarded the role. I'm going to send notifications to {channel.mention}. Finally, I created the role {role.mention} ({role.id}). You may rename this role and customize to any color or icon. Ensure this role is below Melanie at all times so that I can manage it.",
            hide_author=True,
        )
