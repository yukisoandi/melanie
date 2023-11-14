from __future__ import annotations

import discord
from loguru import logger as log
from melaniebot.core import checks, commands
from melaniebot.core.utils.chat_formatting import pagify

from .abc import MixinMeta  # type: ignore


def _(x):
    return x


class SettingsMixin(MixinMeta):
    """All commands for setting up the bot."""

    @commands.group()
    @checks.has_permissions(administrator=True)
    @commands.guild_only()
    async def warnset(self, ctx: commands.Context) -> None:
        """Set all WarnSystem settings."""

    # commands are listed in the alphabetic order, like the help message
    @warnset.command(name="autoupdate")
    async def warnset_autoupdate(self, ctx: commands.Context, enable: bool = None) -> None:
        r"""Defines if the bot should update permissions of new channels for the
        mute role.

        If enabled, for each new text channel and category created, the
        Mute role will be\ denied the permission to send messages and
        add reactions here. Keeping this disabled might cause issues
        with channels created after the WarnSystem setup\ where muted
        members can talk.

        """
        guild = ctx.guild
        current = await self.data.guild(guild).update_mute()
        if enable is None:
            await ctx.send(
                ("The bot currently {update} new channels. If you want to change this, type `;warnset autoupdate {opposite}`.").format(
                    update="updates" if current else ("doesn't update"),
                    opposite=not current,
                ),
            )
        elif enable:
            await self.data.guild(guild).update_mute.set(True)
            await ctx.send("Done. New created channels will be updated to keep the mute role working.")
        else:
            await self.data.guild(guild).update_mute.set(False)
            await ctx.send(
                "Done. New created channels won't be updated.\n**Make sure to update manually new channels to keep the mute role working as intended.**",
            )

    @warnset.command("bandays")
    async def warnset_bandays(self, ctx: commands.Context, ban_type: str, days: int) -> None:
        """Set the number of messages to delete when a member is banned.

        You can set a value for a softban or a ban.
        When invoking the command, you must specify `ban` or `softban` as the first\
        argument to specify which type of ban you want to edit, then a number between\
        1 and 7, for the number of days of messages to delete.
        These values will be always used for level 4/5 warnings.

        __Examples__

        - `;warnset bandays softban 2`
          The number of days of messages to delete will be set to 2 for softbans.

        - `;warnset bandays ban 7`
          The number of days of messages to delete will be set to 7 for bans.

        - `;warnset bandays ban 0`
          The bans will not delete any messages.

        """
        guild = ctx.guild
        if all(ban_type != x for x in ["softban", "ban"]):
            await ctx.send("The first argument must be `ban` or `softban`.\nType `{prefix}help warnset bandays` for more details.")
            return
        if not 0 <= days <= 7:
            is_ban = "You can set 0 to disable messages deletion." if ban_type == "ban" else ""
            await ctx.send("The number of days of messages to delete must be between 1 and 7, due to Discord restrictions.\n" + is_ban)
            return
        if days == 0 and ban_type == "softban":
            await ctx.send(
                "The goal of a softban is to delete the members' messages. Disabling this would make the softban a simple kick. Enter a value between 1 and 7.",
            )
            return
        if ban_type == "softban":
            await self.data.guild(guild).bandays.softban.set(days)
        else:
            await self.data.guild(guild).bandays.ban.set(days)
        await ctx.send("The new value was successfully set!")

    @warnset.command(name="channel")
    async def warnset_channel(self, ctx: commands.Context, channel: discord.TextChannel, level: int = None) -> None:
        """Set the channel for the WarnSystem modlog.

        This will use the Melanie's modlog by default if it was set.

        All warnings will be logged here. I need the `Send Messages` and
        `Embed Links` permissions.

        If you want to set one channel for a specific level of warning,
        you can specify a\\ number after the channel

        """
        guild = ctx.guild
        if not channel.permissions_for(guild.me).send_messages:
            await ctx.send("I don't have the permission to send messages in that channel.")
        elif not channel.permissions_for(guild.me).embed_links:
            await ctx.send("I don't have the permissions to send embed links in that channel.")
        elif not level:
            await self.data.guild(guild).channels.main.set(channel.id)
            await ctx.send(
                f"Done. All events will be send to that channel by default.\n\nIf you want to send a specific warning level in a different channel, you can use the same command with the number after the channel.\nExample: `{ctx.prefix}warnset channel #your-channel 3`",
            )
        elif not 1 <= level <= 5:
            await ctx.send("If you want to specify a level for the channel, provide a number between 1 and 5.")
        else:
            await self.data.guild(guild).channels.set_raw(level, value=channel.id)
            await ctx.send(f"Done. All level {level} warnings events will be sent to that channel.")

    @warnset.command("reason")
    async def warnset_reason(self, ctx: commands.Context) -> None:
        """Require a reason for moderation actions.

        Toggling this setting will require a reason for every moderation
        action.

        """
        current = await self.data.guild(ctx.guild).force_reason()
        if current:
            await ctx.send("Force reason is currently enabled. Setting to disabled.")
            await self.data.guild(ctx.guild).force_reason.set(False)

        if not current:
            await ctx.send("Force reason is currently disabled. Setting to enabled.")
            await self.data.guild(ctx.guild).force_reason.set(True)
            await ctx.tick()

    @warnset.command(name="mute")
    async def warnset_mute(self, ctx: commands.Context, *, role: discord.Role = None) -> None:
        """Create the role used for muting members.

        You can specify a role when invoking the command to specify
        which role should be used. If you don't specify a role, one will
        be created for you.

        """
        guild = ctx.guild
        my_position = guild.me.top_role.position
        if not role:
            if not guild.me.guild_permissions.manage_roles:
                await ctx.send("I can't manage roles, please give me this permission to continue.")
                return
            async with ctx.typing():
                fails = await self.api.maybe_create_mute_role(guild)
                my_position = guild.me.top_role.position
                if fails is False:
                    await ctx.send(
                        "A mute role was already created! You can change it by specifying a role when typing the command.\n`;warnset mute <role name>`",
                    )
                    return
                else:
                    errors = "\n\nSome errors occured when editing the channel permissions:\n" + "\n".join(fails) if fails else ""
                    text = (
                        f"The role `Muted` was successfully created at position {my_position - 1}. Feel free to drag it in the hierarchy and edit its permissions, as long as my top role is above and the members to mute are below."
                        + errors
                    )
                    for page in pagify(text):
                        await ctx.send(page)
        elif role.position >= my_position:
            await ctx.send(
                _('That role is higher than my top role in the hierarchy. Please move it below "{bot_role}".').format(bot_role=guild.me.top_role.name),
            )
        else:
            await self.cache.update_mute_role(guild, role)
            await ctx.send("The new mute role was successfully set!")

    @warnset.command(name="refreshmuterole")
    @commands.cooldown(1, 120, commands.BucketType.guild)
    async def warnset_refreshmuterole(self, ctx: commands.Context) -> None:
        """Refresh the mute role's permissions in the server.

        This will iterate all of your channels and make sure all
        permissions are correctly\\ configured for the mute role.

                The muted role will be prevented from sending messages
        and adding reactions in all text\\ channels, and prevented from
        talking in all voice channels.

        """
        guild = ctx.guild
        mute_role = await self.cache.get_mute_role(guild)
        if mute_role is None:
            await ctx.send(f"No mute role configured on this server. Create one with `{ctx.clean_prefix}warnset mute`.")
            return
        mute_role = guild.get_role(mute_role)
        if not mute_role:
            await ctx.send(f"It looks like the configured mute role was deleted. Create a new one with `{ctx.clean_prefix}warnset mute`.")
            return
        if not guild.me.guild_permissions.manage_channels:
            await ctx.send("I need the `Manage channels` permission to continue.")
            return
        await ctx.send(f"Now checking {len(guild.channels)} channels, please wait...")
        perms = discord.PermissionOverwrite(send_messages=False, add_reactions=False, speak=False)
        reason = "WarnSystem mute role permissions refresh"
        perms_failed = []  # if it failed because of Forbidden, add to this list
        other_failed = []  # if it failed because of HTTPException, add to this one
        count = 0
        category: discord.CategoryChannel
        async with ctx.typing():
            for channel in guild.channels:  # include categories, text and voice channels
                # we check if the perms are correct, to prevent useless API calls
                overwrites = channel.overwrites_for(mute_role)
                if isinstance(channel, discord.TextChannel) and overwrites.send_messages is False and overwrites.add_reactions is False:
                    continue
                elif isinstance(channel, discord.VoiceChannel) and overwrites.speak is False:
                    continue
                elif overwrites == perms:
                    continue
                count += 1
                try:
                    log.debug(f"[Guild {guild.id}] Editing channel {channel.name} for mute role permissions refresh.")
                    await channel.set_permissions(target=mute_role, overwrite=perms, reason=reason)
                except discord.errors.Forbidden:
                    perms_failed.append(channel)
                except discord.errors.HTTPException:
                    log.exception(f"[Guild {guild.id}] Failed to edit channel {channel.name} ({channel.id}) while refreshing the mute role's permissions.")
                    other_failed.append(channel)
        if not perms_failed and not other_failed:
            await ctx.send(f"Successfully checked all channels, {count} were edited.")
            return

        def format_channels(channels: list):
            text = ""
            for channel in sorted(channels, key=lambda x: x.position):
                if isinstance(channel, discord.TextChannel):
                    text += f"- Text channel: {channel.mention}"
                elif isinstance(channel, discord.VoiceChannel):
                    text += f"- Voice channel: {channel.name}"
                else:
                    text += f"- Category: {channel.name}"
            return text

        text = f"Successfully checked all channels, {count - len(perms_failed) - len(other_failed)}/{count} were edited.\n\n"
        if perms_failed:
            text += f"The following channels were not updated due to a permission failure, probably enforced `Manage channels` permission:\n{format_channels(perms_failed)}\n"
        if other_failed:
            text += f"The following channels were not updated due to an unknown error (check your logs or ask the bot administrator):\n{format_channels(other_failed)}\n"
        text += "You can fix these issues and run the command once again."
        for page in pagify(text):
            await ctx.send(page)

    # @warnset.command(name="reinvite")
    # async def warnset_reinvite(self, ctx: commands.Context, enable: bool = None):
    #     """
    #     Set if the bot should send an invite after a temporary ban.

    #     If enabled, any unbanned member will receive a DM with an invite to join back to the server.
    #     The bot needs to share a server with the member to send a DM.

    #     Invoke the command without arguments to get the current status.
    #     """
    #     if enable is None:
    #         await ctx.send(
    #             _(
    #             ).format(respect=("does") if current else ("doesn't"), opposite=not current)
    #         await ctx.send(
    #             _(
    #                 "Done. The bot will try to send an invite to unbanned members. Please note "
    #                 "that the bot needs to share one server in common with the member to receive "
    #                 "the message."

    @warnset.command("removeroles")
    async def warnset_removeroles(self, ctx: commands.Context, enable: bool = None) -> None:
        r"""Defines if the bot should remove all roles on mute.

        If enabled, when you set a level 2 warning on a member, he will
        be assigned the mute role\ as usual, but all of his other roles
        will also be removed. Once the mute ends, the member will get
        his roles back. This can be useful for role permissions issues.

        """
        guild = ctx.guild
        current = await self.data.guild(guild).remove_roles()
        if enable is None:
            await ctx.send(
                ("The bot currently {remove} all roles on mute. If you want to change this, type `;warnset removeroles {opposite}`.").format(
                    remove="removes" if current else ("doesn't remove"),
                    opposite=not current,
                ),
            )
        elif enable:
            await self.data.guild(guild).remove_roles.set(True)
            await ctx.send(
                f"Done. All roles will be removed from muted members. They will get their roles back once the mute ends or when someone removes the warning using the `{ctx.prefix}warnings` command.",
            )
        else:
            await self.data.guild(guild).remove_roles.set(False)
            await ctx.send("Done. Muted members will keep their roles on mute.")

    # @warnset.command(name="settings")
    # async def warnset_settings(self, ctx: commands.Context):
    #     """
    #     Show the current settings.
    #     """
    #     if not ctx.channel.permissions_for(guild.me).embed_links:
    #     async with ctx.typing():

    #         # collect data and make strings
    #         for key, channel in dict(modlog_channels).items():
    #             if not channel:
    #                 if key != "main":
    #             if key == "main":
    #                 channels += ("Level {level} warnings channel: {channel}\n").format(
    #             _(
    #                 "substitutions` to get started."
    #             ).format(prefix=ctx.prefix)
    #             if len_substitutions < 1
    #             else _(
    #             ).format(
    #         for key, description in modlog_dict.items():
    #             if key == "main":
    #         if len(modlog_descriptions) > 1024:
    #         for key, description in user_dict.items():
    #             if key == "main":
    #         if len(user_descriptions) > 1024:
    #                 for level, value in all_data["colors"].items()

    #         # make embed
    #         for embed in embeds:
    #             embed.description = _(
    #             ).format(prefix=ctx.clean_prefix)
    #         embeds[1].add_field(
    #         embeds[1].add_field(
    #         embeds[1].add_field(
    #             ).format(prefix=ctx.clean_prefix),
    #         await ctx.send(
    #             _(
    #                 "Error when sending the message. Check the warnsystem "
    #                 "logs for more informations."

    # @warnset.command(name="showmod")
    # async def warnset_showmod(self, ctx, enable: bool = None):
    #     """
    #     Defines if the responsible moderator should be revealed to the warned member in DM.

    #     If enabled, any warned member will be able to see who warned them, else they won't know.

    #     Invoke the command without arguments to get the current status.
    #     """
    #     if enable is None:
    #         await ctx.send(
    #             _(
    #             ).format(respect=("does") if current else ("doesn't"), opposite=not current)
    #         await ctx.send(
    #             _(
    #                 "Done. The moderator responsible of a warn will now be shown to the warned "
    #                 "member in direct messages."

    # @warnset.group(name="substitutions")
    # async def warnset_substitutions(self, ctx: commands.Context):
    #     """
    #     Manage the reasons' substitutions

    #     A substitution is text replaced by a key you place in your warn reason.

    #     For example, if you set a substitution with the keyword `last warn` associated with the\
    #     text `This is your last warning!`, this is what will happen with your next warnings:

    #     `;warn 4 @annoying_member Stop spamming. [last warn]`
    #     Reason = Stop spamming. This is your last warning!
    #     """
    #     pass

    # @warnset_substitutions.command(name="add")
    # async def warnset_substitutions_add(self, ctx: commands.Context, name: str, *, text: str):
    #     """
    #     Create a new subsitution.

    #     `name` should be something short, it will be the keyword that will be replaced by your text
    #     `text` is what will be replaced by `[name]`

    #     Example:
    #     - `;warnset substitutions add ad Advertising for a Discord server`
    #     - `;warn 1 @noob [ad] + doesn't respect warnings`
    #     The reason will be "Advertising for a Discord server + doesn't respect warnings".
    #     """
    #     async with self.data.guild(ctx.guild).substitutions() as substitutions:
    #         if name in substitutions:
    #             await ctx.send(
    #                 _(
    #                     "The name you're using is already used by another substitution!\n"
    #                     "Delete or edit it with `;warnset substitutions delete`"
    #         if len(text) > 600:
    #     await ctx.send(
    #         _(
    #             "substitutions` subcommands."
    #         ).format(keyword=name, substitution=name, prefix=ctx.prefix)

    # @warnset_substitutions.command(name="delete", aliases=["del"])
    # async def warnset_substitutions_delete(self, ctx: commands.Context, name: str):
    #     """
    #     Delete a previously set substitution.

    #     The substitution must exist, see existing substitutions with the `;warnset substitutions\
    #     list` command.
    #     """
    #     async with self.data.guild(ctx.guild).substitutions() as substitutions:
    #         if name not in substitutions:
    #             await ctx.send(
    #                 _(
    #                     "That substitution doesn't exist!\nSee existing substitutions with the "
    #                 ).format(prefix=ctx.prefix)

    # @checks.is_owner()
    # @warnset_substitutions.command(name="list",hidden=True)
    # async def warnset_substitutions_list(self, ctx: commands.Context):
    #     """
    #     List all existing substitutions on your server
    #     """
    #     if len(substitutions) < 1:
    #         await ctx.send(
    #             _(
    #                 "You don't have any existing substitution on this server!\n"
    #             ).format(prefix=ctx.prefix)
    #     for substitution, content in substitutions.items():
    #     for i, page in enumerate(messages):
    #         await ctx.send(

    # @checks.is_owner()
    # @warnset.command(name="thumbnail",hidden=True)
    # async def warnset_thumbnail(self, ctx: commands.Context, level: int, url: str = None):
    #     """
    #     Edit the image displayed on the embeds.

    #     This is common to the modlog embeds and the embeds sent to the members.
    #     The URL must be the direct link to the image.
    #     You can omit the URL if you want to remove any image on the embed.
    #     """
    #     if not 1 <= level <= 5:
    #     await ctx.send(
    #         ("The new image for level {level} warnings has been set to {image}.").format(
