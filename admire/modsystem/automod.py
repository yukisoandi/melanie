from __future__ import annotations

from datetime import timedelta

import discord
from melaniebot.core import commands
from melaniebot.core.commands.converter import TimedeltaConverter
from melaniebot.core.utils.predicates import MessagePredicate

from .abc import MixinMeta  # type: ignore


def _(x):
    return x


class AutomodMixin(MixinMeta):
    """Automod configuration."""

    async def _ask_for_value(
        self,
        ctx: commands.Context,
        bot_msg: discord.Message,
        embed: discord.Embed,
        description: str,
        need: str = "same_context",
        optional: bool = False,
    ):
        embed.description = description
        if optional:
            embed.set_footer(text=_('\n\nType "skip" to omit this parameter.'))
        await bot_msg.edit(content="", embed=embed)
        pred = getattr(MessagePredicate, need, MessagePredicate.same_context)(ctx)
        user_msg = await self.bot.wait_for("message", check=pred, timeout=30)
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await user_msg.delete()
        if optional and user_msg.content == "skip":
            return None
        if need == "time":
            try:
                time = await TimedeltaConverter().convert(ctx, user_msg.content)
            except commands.BadArgument:
                await ctx.send("Invalid time format.")
                return await self._ask_for_value(ctx, bot_msg, embed, description, need, optional)
            else:
                return time
        if need == "same_context":
            return user_msg.content
        return pred.result

    def _format_embed_for_autowarn(
        self,
        embed: discord.Embed,
        number_of_warns: int,
        warn_level: int,
        warn_reason: str,
        lock_level: int,
        only_automod: bool,
        time: timedelta,
        duration: timedelta,
    ) -> discord.Embed:
        time_str = self.api._format_timedelta(time) if time else ("Not set.")
        duration_str = self.api._format_timedelta(duration) if duration else ("Not set.")

        embed.description = f"Number of warnings until action: {number_of_warns}\n" + f"Warning level: {warn_level}\n"
        embed.description += f"Warning reason: {warn_reason}\n"
        embed.description += f"Time interval: {time_str}\n"
        if warn_level in {2, 5}:
            embed.description += f"Duration: {duration_str}\n"
        embed.description += f"Lock to level: {'disabled' if lock_level == 0 else lock_level}\n"
        embed.description += f"Only count automod: {'yes' if only_automod else ('no')}\n\n"
        embed.add_field(
            name="What will happen:",
            value=(
                "If a member receives {number}{level_lock} warnings{from_bot}{within_time}, the bot will set a level {level} warning on him{duration} for the reason: {reason}"
            ).format(
                number=number_of_warns,
                level_lock=f" level {lock_level}" if lock_level else "",
                from_bot=" from the automod" if only_automod else "",
                within_time=f" within {time_str}" if time else "",
                level=warn_level,
                duration=f" during {duration_str}" if duration else "",
                reason=warn_reason,
            ),
            inline=False,
        )
        return embed

    # @commands.group()
    # @checks.admin_or_permissions(administrator=True)
    # async def automod(self, ctx: commands.Context) -> None:
    #     """
    #     WarnSystem automod configuration.
    #     """

    # @automod.command(name="enable")
    # async def automod_enable(self, ctx: commands.Context, confirm: bool = None) -> None:
    #     """
    #     Enable or disable WarnSystem's automod.
    #     """
    #     if confirm is None:
    #         await ctx.send(
    #             ("Automod is currently {state}.\nType `{prefix}automod enable {arg}` to {action} it.").format(

    #         if not self.cache.automod_enabled:
    #         if not self.cache.automod_enabled:

    # @automod.group(name="regex")
    # async def automod_regex(self, ctx: commands.Context) -> None:
    #     """
    #     Trigger warnings when a regular expression matches a message like
    #     ReTrigger.
    #     """

    # @automod_regex.command(name="add")
    # async def automod_regex_add(
    #     self, ctx: commands.Context, name: str, regex: ValidRegex, level: int, time: Optional[TimedeltaConverter], *, reason: str
    # ) -> None:
    #     """
    #     Create a new Regex trigger for a warning.

    #             Use https://regex101.com/ to test your expression.

    #             Possible keywords:
    #             - `{member}`
    #             - `{channel}`
    #             - `{guild}`

    #     1 Discord invite sent in {channel.mention}.`

    #     """
    #     if name in automod_regex:
    #     if time:

    # @automod_regex.command(name="delete", aliases=["del", "remove"])
    # async def automod_regex_delete(self, ctx: commands.Context, name: str) -> None:
    #     """
    #     Delete a Regex trigger.
    #     """
    #     if name not in await self.cache.get_automod_regex(guild):

    # @automod_regex.command(name="list")
    # async def automod_regex_list(self, ctx: commands.Context) -> None:
    #     """
    #     Lists all Regex triggers.
    #     """
    #     if not automod_regex:

    # @automod_regex.command(name="show")
    # async def automod_regex_show(self, ctx: commands.Context, name: str) -> None:
    #     """
    #     Show details of a Regex trigger.
    #     """
    #     embed.add_field(
    #         ),

    # @automod.group(name="warn")
    # async def automod_warn(self, ctx: commands.Context) -> None:
    #     """
    #     Trigger actions when a member get x warnings within the specified time.

    #     For example, if a member gets 3 warnings within a day, you can
    #     make the bot automatically \ set him a level 3 warning with the
    #     given reason.         It is also possible to only include
    #     warnings given by the bot when counting.

    #     """

    # @automod_warn.command(name="add")
    # async def automod_warn_add(self, ctx: commands.Context) -> None:
    #     """
    #     Create a new automated warn based on member's modlog.

    #     Multiple parameters are needed, you will open an interactive
    #     menu.

    #     """
    #         while True:
    #             if number_of_warns > 1:
    #         while True:
    #             if 1 <= warn_level <= 5:
    #         time: timedelta = await self._ask_for_value(
    #             ctx,
    #             msg,
    #             embed,
    #                 "For how long should this automod be active?\n\nFor example, you can make it trigger if a member got 3 warnings __within a"
    #                 " day__\nOmitting this value will make the automod look across the entire member's modlog without time limit.\n\nFormat is the"
    #             ),
    #         if warn_level in [2, 5]:
    #             duration: timedelta = await self._ask_for_value(
    #                 ctx,
    #                 msg,
    #                 embed,
    #                     " punished?\nSkip this value to make the mute/ban unlimited.\nTime format is the same as the previous question."
    #                 ),
    #         while True:
    #                 ctx,
    #                 msg,
    #                 embed,
    #                     " to disable."
    #                 ),
    #             if 0 <= lock_level <= 5:
    #             ctx,
    #             msg,
    #             embed,
    #                 "Should the automod be triggered only by other automod warnings?\nIf enabled, warnings issued by a normal moderator will not be"
    #                 " added to the count.\n\nType `yes` or `no`."
    #             ),
    #     if not pred.result:
    #     async with self.data.guild(guild).automod.warnings() as warnings:
    #         warnings.append(

    # @automod_warn.command(name="delete", aliases=["del", "remove"])
    # async def automod_warn_delete(self, ctx: commands.Context, index: int) -> None:
    #     """
    #     Delete an automated warning.

    #     You can find the index with the `;automod warn list` command.

    #     """
    #     if index < 0:
    #     async with self.data.guild(guild).automod.warnings() as warnings:
    #             embed,
    #         if not pred.result:

    # @automod_warn.command(name="list")
    # async def automod_warn_list(self, ctx: commands.Context) -> None:
    #     """
    #     Lists automated warnings on this server.
    #     """
    #     if not autowarns:
    #         for index, data in enumerate(autowarns)

    #         + page
    #         for i, page in enumerate(text)

    # @automod_warn.command(name="show")
    # async def automod_warn_show(self, ctx: commands.Context, index: int) -> None:
    #     """
    #     Shows the contents of an automatic warn.

    #     Index is shown by the `;automod warn list` command.

    #     """
    #     if index < 0:
    #     async with self.data.guild(guild).automod.warnings() as warnings:
    #         embed,

    # @automod.group(name="antispam")
    # async def automod_antispam(self, ctx: commands.Context) -> None:
    #     """
    #     Configure the antispam system.

    #     If you installed WarnSystem for the sole purpose of antispam,
    #     disable all warnings and\ you shouldn't need further setup.

    #     """

    # @automod_antispam.command(name="enable")
    # async def automod_antispam_enable(self, ctx: commands.Context, enable: bool = None) -> None:
    #     """
    #     Enable WarnSystem's antispam.
    #     """
    #     if enable is None:
    #         if status:
    #         await ctx.send(
    #                 "WarnSystem's antispam feature will make your text channels cleaner by removing and warning members sending undesired"
    #             ).format(prefix=ctx.clean_prefix, status=status, status_change=status_change, setting=setting)
    #     if await self.data.guild(guild).automod.enabled() is False and enable:

    # @automod_antispam.command(name="threshold")
    # async def automod_antispam_threshold(self, ctx: commands.Context, max_messages: int, delay: int) -> None:
    #     """
    #     Defines the spam threshold.

    #             Delay is in seconds.
    #     before triggering the antispam.

    #     """
    #     await ctx.send(
    #         ("Done. A member will be considered as spamming if he sends more than {max_messages} within {delay} seconds.").format(

    # @automod_antispam.command(name="delay")
    # async def automod_antispam_delay(self, ctx: commands.Context, delay: int) -> None:
    #     """
    #     If antispam is triggered twice within this delay, perform the warn.

    #             Delay in seconds.
    #             If the antispam is triggered once, a simple warning is send in the chat, mentionning the\
    #     member. If the same member triggers the antispam system a second time within this delay, there\
    #     will be an actual warning taken, the one you define with `;automod antispam warn`.

    #             This is a way to tell the member he is close to being sanctioned. Of course you can\
    #     disable this and immediatly take actions by setting a delay of 0. Default is 60 seconds.

    #     """
    #     if delay:
    #         await ctx.send(
    #                 " define the warn taken."
    #             ).format(time=delay, prefix=ctx.clean_prefix)
    #         await ctx.send(
    #             ("Done. When triggered, the antispam will immediately perform the warn you defined with `{prefix}automod antispam warn`.").format(

    # @automod_antispam.command(name="warn")
    # async def automod_antispam_warn(self, ctx: commands.Context, level: int, duration: Optional[TimedeltaConverter], *, reason: str) -> None:
    #     """
    #     Define the warn taken when the antispam is triggered.

    #             The arguments for this command works the same way as the warn command.
    #             Examples: `;automod antispam warn 1 Spamming` `;automod antispam warn 2 30m Spamming`

    #             You can use the `;automod warn` command to configure an automatic warning after multiple\
    #     automod infractions, like a mute after 3 warns.

    #     """
    #     await self.data.guild(guild).automod.antispam.warn.set(
    #     await ctx.send(
    #         ).format(

    # @automod_antispam.command(name="info")
    # async def automod_antispam_info(self, ctx: commands.Context) -> None:
    #     """
    #     Show infos about the antispam system.
    #     """
    #     if automod_enabled:
    #     embed.add_field(
    #         ).format(
    #         ),
    #     if level in [2, 5]:
    #             if antispam_settings["warn"]["time"]
    #             else ("Unlimited.")
    #     embed.add_field(
    #         ),
