from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from loguru import logger as log
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie as Bot
from melaniebot.core.commands.converter import TimedeltaConverter
from melaniebot.core.utils.chat_formatting import pagify
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu

from melanie import footer_gif

from .converter import Args
from .objects import Giveaway, GiveawayEnterError, GiveawayExecError

GIVEAWAY_KEY = "giveaways"

# TODO: Add a way to delete giveaways that have ended from the config


class Giveaways(commands.Cog):
    """Giveaway Commands."""

    __version__ = "0.11.5"

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.config = Config.get_conf(self, identifier=95932766180343808)
        self.config.init_custom(GIVEAWAY_KEY, 2)
        self.giveaways = {}
        self.session = aiohttp.ClientSession()

        self.giveaway_bgloop = asyncio.create_task(self.init())
        with contextlib.suppress(Exception):
            self.bot.add_dev_env_value("giveaways", lambda x: self)

    async def init(self) -> None:
        await self.bot.wait_until_ready()
        data = await self.config.custom(GIVEAWAY_KEY).all()
        for giveaways in data.values():
            for msgid, giveaway in giveaways.items():
                if giveaway.get("ended", False):
                    continue
                if datetime.now(timezone.utc) > datetime.fromtimestamp(giveaway["endtime"]).replace(tzinfo=timezone.utc):
                    continue
                self.giveaways[int(msgid)] = Giveaway(
                    guildid=giveaway["guildid"],
                    channelid=giveaway["channelid"],
                    messageid=msgid,
                    endtime=datetime.fromtimestamp(giveaway["endtime"]).replace(tzinfo=timezone.utc),
                    prize=giveaway["prize"],
                    emoji=giveaway.get("emoji", "🎉"),
                    entrants=giveaway["entrants"],
                    **giveaway["kwargs"],
                )
        while True:
            try:
                await self.check_giveaways()
            except Exception:
                log.exception("Exception in giveaway loop: ")
            await asyncio.sleep(60)

    def cog_unload(self) -> None:
        with contextlib.suppress(Exception):
            self.bot.remove_dev_env_value("giveaways")
        self.giveaway_bgloop.cancel()

    async def check_giveaways(self) -> None:
        to_clear = []
        for msgid, giveaway in self.giveaways.items():
            if giveaway.endtime < datetime.now(timezone.utc):
                await self.draw_winner(giveaway)
                to_clear.append(msgid)

                gw = await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(msgid)).all()
                gw["ended"] = True
                await self.config.custom(GIVEAWAY_KEY, giveaway.guildid, str(msgid)).set(gw)

        for msgid in to_clear:
            del self.giveaways[msgid]

    async def draw_winner(self, giveaway: Giveaway) -> None:
        guild = self.bot.get_guild(giveaway.guildid)
        if guild is None:
            return
        channel_obj = guild.get_channel(giveaway.channelid)
        if channel_obj is None:
            return

        winners = giveaway.draw_winner()
        winner_objs = None
        if winners is None:
            txt = "Not enough entries to roll the giveaway."
        else:
            winner_objs = []
            txt = ""
            for winner in winners:
                winner_obj = guild.get_member(winner)
                if winner_obj is None:
                    txt += f"{winner} (Not Found)\n"
                else:
                    txt += f"{winner_obj.mention}\n"
                    winner_objs.append(winner_obj)

        msg = channel_obj.get_partial_message(giveaway.messageid)
        winners = giveaway.kwargs.get("winners", 1) or 1
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{giveaway.prize}",
            description=f"Winner(s):\n{txt}",
            color=await self.bot.get_embed_color(channel_obj),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Reroll: {(await self.bot.get_prefix(msg))[-1]}gw reroll {giveaway.messageid} | Ended at")
        try:
            await msg.edit(content="🎉 Giveaway Ended 🎉", embed=embed)
        except discord.NotFound:
            return
        if giveaway.kwargs.get("announce"):
            announce_embed = discord.Embed(
                title="Giveaway Ended",
                description=f"Congratulations to the {f'{str(winners)} ' if winners > 1 else ''}winner{'s' if winners > 1 else ''} of [{giveaway.prize}]({msg.jump_url}).\n{txt}",
                color=await self.bot.get_embed_color(channel_obj),
            )

            announce_embed.set_footer(text=f"Reroll: {(await self.bot.get_prefix(msg))[-1]}gw reroll {giveaway.messageid}")
            await channel_obj.send(
                content="Congratulations " + ",".join([x.mention for x in winner_objs]) if winner_objs is not None else "",
                embed=announce_embed,
            )
        if channel_obj.permissions_for(guild.me).manage_messages:
            await msg.clear_reactions()
        if winner_objs is not None:
            if giveaway.kwargs.get("congratulate", False):
                for winner in winner_objs:
                    with contextlib.suppress(discord.Forbidden):
                        await winner.send(f"Congratulations! You won {giveaway.prize} in the giveaway on {guild}!")
            async with self.config.custom(GIVEAWAY_KEY, giveaway.guildid, int(giveaway.messageid)).entrants() as entrants:
                entrants = [x for x in entrants if x != winner]
        return

    @commands.group(aliases=["gw"])
    @commands.has_permissions(manage_guild=True)
    async def giveaway(self, ctx: commands.Context) -> None:
        """Manage the giveaway system."""

    @giveaway.command()
    async def start(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel],
        time: TimedeltaConverter(default_unit="minutes"),
        *,
        prize: str,
    ) -> None:
        """Start a giveaway.

        This by default will DM the winner and also DM a user if they
        cannot enter the giveaway.

        """
        channel = channel or ctx.channel
        end = datetime.now(timezone.utc) + time
        embed = discord.Embed(title=f"{prize}", description=f"\nReact with 🎉 to enter\nEnds: <t:{int(end.timestamp())}:R>", color=await ctx.embed_color())
        msg = await channel.send(embed=embed)
        giveaway_obj = Giveaway(ctx.guild.id, channel.id, msg.id, end, prize, "🎉", **{"congratulate": True, "notify": True})
        self.giveaways[msg.id] = giveaway_obj
        await msg.add_reaction("🎉")
        giveaway_dict = deepcopy(giveaway_obj.__dict__)
        giveaway_dict["endtime"] = giveaway_dict["endtime"].timestamp()
        await self.config.custom(GIVEAWAY_KEY, str(ctx.guild.id), str(msg.id)).set(giveaway_dict)

    @giveaway.command()
    async def reroll(self, ctx: commands.Context, msgid: int):
        """Reroll a giveaway."""
        data = await self.config.custom(GIVEAWAY_KEY, ctx.guild.id).all()
        if str(msgid) not in data:
            return await ctx.send("Giveaway not found.")
        if msgid in self.giveaways:
            return await ctx.send(f"Giveaway already running. Please wait for it to end or end it via `{ctx.clean_prefix}gw end {msgid}`.")
        giveaway_dict = data[str(msgid)]
        giveaway_dict["endtime"] = datetime.fromtimestamp(giveaway_dict["endtime"]).replace(tzinfo=timezone.utc)
        giveaway = Giveaway(**giveaway_dict)
        try:
            await self.draw_winner(giveaway)
        except GiveawayExecError as e:
            await ctx.send(e.message)
        else:
            await ctx.tick()

    @giveaway.command(aliases=["cancel", "stop"])
    async def end(self, ctx: commands.Context, msgid: int):
        """End a giveaway."""
        if msgid in self.giveaways:
            if self.giveaways[msgid].guildid != ctx.guild.id:
                return await ctx.send("Giveaway not found.")
            await self.draw_winner(self.giveaways[msgid])
            del self.giveaways[msgid]
            gw = await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).all()
            gw["ended"] = True
            await self.config.custom(GIVEAWAY_KEY, ctx.guild.id, str(msgid)).set(gw)
            await ctx.tick()
        else:
            await ctx.send("Giveaway not found.")

    @giveaway.command(aliases=["adv"])
    async def advanced(self, ctx: commands.Context, *, arguments: Args) -> None:
        """Advanced creation of Giveaways.

        `;gw explain` for a further full listing of the arguments.

        """
        prize = f"**{arguments['prize']}**"
        duration = arguments["duration"]
        channel = arguments["channel"] or ctx.channel

        winners = arguments.get("winners", 1) or 1
        end = datetime.now(timezone.utc) + duration
        description = arguments["description"] or ""
        if arguments["show_requirements"]:
            description += "\n\n**Requirements**:"
            for kwarg in set(arguments) - {
                "show_requirements",
                "prize",
                "duration",
                "channel",
                "winners",
                "description",
                "congratulate",
                "notify",
                "announce",
                "emoji",
            }:
                if arguments[kwarg]:
                    subject = ""
                    kwtitle = kwarg.title()
                    if kwtitle == "Created":
                        kwvalue = arguments[kwarg]
                        kwtitle = "Account Age"
                        subject = "days"

                    elif kwtitle == "Joined":
                        kwvalue = arguments[kwarg]
                        kwtitle = "Member of Server"
                        subject = "days"

                    elif kwtitle == "Roles":
                        roles = arguments[kwarg]
                        kwvalue = "".join(f"{ctx.guild.get_role(r).mention} " for r in roles)
                        kwtitle = "Required Roles"
                        subject = ""

                    elif kwtitle == "Server":
                        server_id = int(arguments[kwarg][0])
                        kwvalue = f"**{str(self.bot.get_guild(server_id))}**"
                        kwtitle = "Must be a member of"
                        subject = ""
                    else:
                        kwvalue = arguments[kwarg]
                        kwtitle = kwarg.title()
                        subject = "days"
                    description += f"\n{kwtitle}: {kwvalue} {subject}"

        emoji = arguments["emoji"] or "🎉"
        if isinstance(emoji, int):
            emoji = self.bot.get_emoji(emoji)
        embed = discord.Embed(
            title=f"{f'{winners}x ' if winners > 1 else ''}{prize}",
            description=f"{description}\n\nReact with {emoji} to enter\n\nEnds: <t:{int(end.timestamp())}:R>",
            color=await ctx.embed_color(),
        )
        txt = "\n"
        if arguments["ateveryone"]:
            txt += "@everyone "
        if arguments["athere"]:
            txt += "@here "
        if arguments["mentions"]:
            for mention in arguments["mentions"]:
                role = ctx.guild.get_role(mention)
                if role is not None:
                    txt += f"{role.mention} "
        msg = await channel.send(
            content=f"{emoji} Giveaway! {emoji}{txt}",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=bool(arguments["mentions"]), everyone=bool(arguments["ateveryone"])),
        )

        giveaway_obj = Giveaway(
            ctx.guild.id,
            channel.id,
            msg.id,
            end,
            prize,
            str(emoji),
            **{k: v for k, v in arguments.items() if k not in ["prize", "duration", "channel", "emoji"]},
        )
        self.giveaways[msg.id] = giveaway_obj
        await msg.add_reaction(emoji)
        giveaway_dict = deepcopy(giveaway_obj.__dict__)
        giveaway_dict["endtime"] = giveaway_dict["endtime"].timestamp()
        await self.config.custom(GIVEAWAY_KEY, str(ctx.guild.id), str(msg.id)).set(giveaway_dict)

    @giveaway.command()
    async def entrants(self, ctx: commands.Context, msgid: int):
        """List all entrants for a giveaway."""
        if msgid not in self.giveaways:
            return await ctx.send("Giveaway not found.")
        giveaway = self.giveaways[msgid]
        if not giveaway.entrants:
            return await ctx.send("No entrants.")
        count = {}
        for entrant in giveaway.entrants:
            if entrant not in count:
                count[entrant] = 1
            else:
                count[entrant] += 1
        msg = ""
        for userid, count_int in count.items():
            user = ctx.guild.get_member(userid)
            msg += f"{user.mention} ({count_int})\n" if user else (f"<{userid}> ({count_int})\n")
        embeds = []
        for page in pagify(msg, delims=["\n"], page_length=800):
            embed = discord.Embed(title="Entrants", description=page, color=await ctx.embed_color())
            embed.set_footer(text=f"Total entrants: {len(count)}")
            embeds.append(embed)

        if len(embeds) == 1:
            return await ctx.send(embed=embeds[0])
        return await menu(ctx, embeds, DEFAULT_CONTROLS)

    @giveaway.command()
    async def info(self, ctx: commands.Context, msgid: int):
        """Information about a giveaway."""
        if msgid not in self.giveaways:
            return await ctx.send("Giveaway not found.")

        giveaway = self.giveaways[msgid]
        winners = giveaway.kwargs.get("winners", 1) or 1
        msg = f"**Entrants:**: {len(giveaway.entrants)}\n**End**: <t:{int(giveaway.endtime.timestamp())}:R>\n"
        for kwarg in giveaway.kwargs:
            if giveaway.kwargs[kwarg]:
                msg += f"**{kwarg.title()}:** {giveaway.kwargs[kwarg]}\n"
        embed = discord.Embed(title=f"{f'{winners}x ' if winners > 1 else ''}{giveaway.prize}", color=await ctx.embed_color(), description=msg)
        embed.set_footer(text=f"Giveaway ID #{msgid}")
        await ctx.send(embed=embed)

    @giveaway.command(name="list")
    async def _list(self, ctx: commands.Context):
        """List all giveaways in the server."""
        if not self.giveaways:
            return await ctx.send("No giveaways are running.")
        giveaways = {x: self.giveaways[x] for x in self.giveaways if self.giveaways[x].guildid == ctx.guild.id}
        if not giveaways:
            return await ctx.send("No giveaways are running.")
        msg = "".join(
            f"{msgid}: [{giveaways[msgid].prize}](https://discord.com/channels/{value.guildid}/{giveaways[msgid].channelid}/{msgid})\n"
            for msgid, value in giveaways.items()
        )

        embeds = []
        for page in pagify(msg, delims=["\n"]):
            embed = discord.Embed(title=f"Giveaways in {ctx.guild}", description=page, color=await ctx.embed_color())
            embeds.append(embed)
        if len(embeds) == 1:
            return await ctx.send(embed=embeds[0])
        return await menu(ctx, embeds, DEFAULT_CONTROLS)

    @giveaway.command()
    async def explain(self, ctx: commands.Context) -> None:
        """Explanation of giveaway advanced and the arguements it supports."""
        # `--joined`: How long the user must be a member of the server for to enter the giveaway. Must be a positive number of days.
        # `--server`: The server ID of a server the user must be a member of to entry the giveaway.

        msg = f"""
        Required arguments:
        `--prize`: The prize to be won.

        Required mutually exclusive arguments (only select 1):
        `--duration`: The duration of the giveaway. Such as `2d3h30m`
        `--end`: The end time of the giveaway. Such as `2021-12-23T30:00:00.000Z`, `tomorrow at 3am`, `in 4 hours`.

        Optional arguments:
        `--channel`: The channel to post the giveaway in. Defaults to current channel.
        `--emoji`: The emoji to use for the giveaway.
        `--roles`: Roles that the giveaway will be restricted to. If the role contains a space, use their ID.
        `--created`: How long the user has been on discord for to enter the giveaway. Must be a positive number of days.
        `--blacklist`: Blacklisted roles that cannot enter the giveaway. If the role contains a space, use their ID.
        `--winners`: How many winners to draw. Must be a positive number.
        `--mentions`: Roles to mention in the giveaway notice.
        `--description`: Description of the giveaway.

        Setting Arguments:
        `--congratulate`: Whether or not to congratulate the winner. Not passing will default to off.
        `--notify`: Whether or not to notify a user if they failed to enter the giveaway. Not passing will default to off.
        `--announce`: Whether to post a seperate message when the giveaway ends. Not passing will default to off.
        `--ateveryone`: Whether to tag @everyone in the giveaway notice.
        `--show-requirements`: Whether to show the requirements of the giveaway.

        Example:
        `{ctx.clean_prefix}gw advanced --prize A better sword --duration 2h3h30m --channel channel-name  --joined 50 --congratulate --notify --multientry `"""
        embed = discord.Embed(title="Giveaway Advanced Explanation", description=msg, color=await ctx.embed_color())
        embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self.giveaways:
            giveaway = self.giveaways[payload.message_id]
            if payload.emoji.is_custom_emoji() and str(payload.emoji) != giveaway.emoji:
                return
            elif payload.emoji.is_unicode_emoji() and str(payload.emoji) != giveaway.emoji:
                return
            try:
                await giveaway.add_entrant(payload.member, bot=self.bot, session=self.session)
            except GiveawayEnterError as e:
                channel = self.bot.get_channel(payload.channel_id)
                message: discord.Message = discord.PartialMessage(channel=channel, id=payload.message_id)
                await message.remove_reaction(payload.emoji, payload.member)
                if giveaway.kwargs.get("notify", False):
                    await payload.member.send(e.message)
                return
            except GiveawayExecError:
                log.exception("Error while adding user to giveaway")
                return
            await self.config.custom(GIVEAWAY_KEY, payload.guild_id, payload.message_id).entrants.set(self.giveaways[payload.message_id].entrants)
