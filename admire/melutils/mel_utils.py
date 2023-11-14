from __future__ import annotations

import asyncio
import colorsys
import inspect
import time
from contextlib import suppress as sps
from fractions import Fraction
from random import choice
from typing import Optional, Union

import discord
import ujson as json
import yarl
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import chat_formatting as cf
from melaniebot.core.utils import chat_formatting as chat
from melaniebot.core.utils.chat_formatting import (
    bold,
    humanize_number,
    humanize_timedelta,
)
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, close_menu, menu
from melaniebot.core.utils.mod import get_audit_reason
from TagScriptEngine import Interpreter, block

from melanie import create_task, footer_gif
from melanie.helpers import get_image_colors2, make_e
from modsystem.iterators import BanIterator

from .common_variables import CHANNEL_TYPE_EMOJIS
from .menus import ActivityPager, BaseMenu, PagePager
from .utils import (
    DEFAULT_GLOBAL,
    FEATURES,
    VC_REGIONS,
    VERIF,
    Route,
    bool_emojify,
    category_format,
    channels_format,
    dynamic_time,
    rgb_to_cmyk,
    rgb_to_hsv,
    sort_channels,
)


def float_to_ratio(value: float) -> str:
    return str(Fraction(value).limit_denominator())


class Utilities(commands.Cog):
    """Useful commands for server administrators."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.request = bot.http.request
        self.TIME_FORMAT = "%d.%m.%Y %H:%M:%S %Z"
        blocks = [block.MathBlock(), block.RandomBlock(), block.RangeBlock()]
        self.engine = Interpreter(blocks)
        self.config = Config.get_conf(self, 95932766180343808, force_registration=True)
        self.config.register_global(**DEFAULT_GLOBAL)
        self.emojis = create_task(self.init())

    async def init(self) -> None:
        await self.gen_emojis()

    @commands.command(name="inviteinfo", aliases=["ii"])
    async def _inviteinfo(self, ctx, code: str):
        """Fetch information on a server from its invite/vanity code."""
        if "/" in code:
            code = code.split("/", -1)[-1].replace(" ", "")

        try:
            invite = await ctx.bot.fetch_invite(code)
        except discord.NotFound:
            return await ctx.send(embed=make_e("Invalid invite"))
        members_total = f"{invite.approximate_member_count:,}"
        members_online_total = f"{invite.approximate_presence_count:,}"
        embed = discord.Embed(title=f"Invite Info: {invite.guild}")
        owner_string = f"**Owner:** {guild.owner}\n**Owner ID:** {guild.owner_id}\n" if (guild := self.bot.get_guild(invite.guild.id)) else ""
        ratio_string = round(invite.approximate_presence_count / invite.approximate_member_count, 2) * 100
        embed.description = f"**ID:** `{invite.guild.id}`\n**Created:** <t:{str(invite.guild.created_at.timestamp()).split('.')[0]}> (<t:{str(invite.guild.created_at.timestamp()).split('.')[0]}:R>)\n{owner_string}**Members:** {members_total}\n**Members Online:** {members_online_total}\n**Online Percent:** {ratio_string}\n**Verification Level:** {str(invite.guild.verification_level).title()}\n\n**Channel Name:** {invite.channel} (`{invite.channel.type}`)\n**Channel ID:** `{invite.channel.id}`\n**Invite Created:**<t:{str(invite.channel.created_at.timestamp()).split('.')[0]}> (<t:{str(invite.channel.created_at.timestamp()).split('.')[0]}:R>)\n"
        urls = ""

        if invite.guild.icon:
            icon_url = yarl.URL(str(invite.guild.icon_url))
            if "a_" in icon_url.path:
                icon_url = str(icon_url).replace("webp", "gif")
            urls += f"[**icon**]({icon_url}), "
            embed.set_thumbnail(url=icon_url)
        if invite.guild.banner:
            banner_url = yarl.URL(str(invite.guild.banner_url))
            if "a_" in banner_url.path:
                banner_url = str(banner_url).replace("webp", "gif")
            urls += f"[**banner**]({banner_url}), "
            lookup = await get_image_colors2(str(invite.guild.banner_url))
            if lookup:
                embed.color = lookup.dominant.decimal
            embed.set_image(url=str(banner_url))

        if invite.guild.splash:
            urls += f"[**splash**]({invite.guild.splash_url}), "
        if len(urls) > 0:
            embed.add_field(name="**assets**", value=urls[:-2], inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    async def serverinfo(self, ctx) -> None:  # sourcery no-metrics
        """Show server information.

        `details`: Shows more information when set to `True`.
        Default to False.

        """
        guild = ctx.guild
        passed = (ctx.message.created_at - guild.created_at).days
        created_at = ("Created on {date}. That's over {num} days ago!").format(date=guild.created_at.strftime("%d %b %Y %H:%M"), num=humanize_number(passed))
        online = humanize_number(len([m.status for m in guild.members if m.status != discord.Status.offline]))
        total_users = humanize_number(guild.member_count)
        text_channels = humanize_number(len(guild.text_channels))
        voice_channels = humanize_number(len(guild.voice_channels))

        def _size(num: int) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1024.0:
                    return f"{num:.1f}{unit}"
                num /= 1024.0
            return f"{num:.1f}{'YB'}"

        def _bitsize(num: int) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1000.0:
                    return f"{num:.1f}{unit}"
                num /= 1000.0
            return f"{num:.1f}{'YB'}"

        shard_info = ""
        online_stats = {
            "Humans: ": lambda x: not x.bot,
            " • Bots: ": lambda x: x.bot,
            "\N{LARGE GREEN CIRCLE}": lambda x: x.status is discord.Status.online,
            "\N{LARGE ORANGE CIRCLE}": lambda x: x.status is discord.Status.idle,
            "\N{LARGE RED CIRCLE}": lambda x: x.status is discord.Status.do_not_disturb,
            "\N{MEDIUM WHITE CIRCLE}\N{VARIATION SELECTOR-16}": lambda x: (x.status is discord.Status.offline),
            "\N{LARGE PURPLE CIRCLE}": lambda x: any(a.type is discord.ActivityType.streaming for a in x.activities),
            "\N{MOBILE PHONE}": lambda x: x.is_on_mobile(),
        }
        member_msg = ("Users online: **{online}/{total_users}**\n").format(online=online, total_users=total_users)
        count = 1
        for emoji, value in online_stats.items():
            try:
                num = len([m for m in guild.members if value(m)])
            except Exception as error:
                log.info(error)
                continue
            else:
                member_msg += f"{emoji} {bold(humanize_number(num))} " + ("\n" if count % 2 == 0 else "")
            count += 1

        guild_features_list = [f"\N{WHITE HEAVY CHECK MARK} {name}" for feature, name in FEATURES.items() if feature in guild.features]

        joined_on = ("{bot_name} joined this server on {bot_join}. That's over {since_join} days ago!").format(
            bot_name=ctx.bot.user.name,
            bot_join=guild.me.joined_at.strftime("%d %b %Y %H:%M:%S"),
            since_join=humanize_number((ctx.message.created_at - guild.me.joined_at).days),
        )

        data = discord.Embed(description=(f"{guild.description}\n\n" if guild.description else "") + created_at, colour=await ctx.embed_colour())
        data.set_author(
            name=guild.name,
            icon_url=(
                "https://cdn.discordapp.com/emojis/457879292152381443.png"
                if "VERIFIED" in guild.features
                else "https://cdn.discordapp.com/emojis/508929941610430464.png"
                if "PARTNERED" in guild.features
                else discord.Embed.Empty
            ),
        )
        if guild.icon_url:
            data.set_thumbnail(url=guild.icon_url)
        data.add_field(name="Members:", value=member_msg)
        data.add_field(
            name="Channels:",
            value=("\N{SPEECH BALLOON} Text: {text}\n\N{SPEAKER WITH THREE SOUND WAVES} Voice: {voice}").format(
                text=bold(text_channels),
                voice=bold(voice_channels),
            ),
        )
        data.add_field(
            name="Utility:",
            value=("Owner: {owner}\nVoice region: {region}\nVerif. level: {VERIF}\nServer ID: {id}{shard_info}").format(
                owner=bold(str(guild.owner)),
                region=f"**{VC_REGIONS.get(str(guild.region)) or str(guild.region)}**",
                VERIF=bold(VERIF[str(guild.verification_level)]),
                id=bold(str(guild.id)),
                shard_info=shard_info,
            ),
            inline=False,
        )
        data.add_field(
            name="Misc:",
            value=("AFK channel: {afk_chan}\nAFK timeout: {afk_timeout}\nCustom emojis: {emoji_count}\nRoles: {role_count}").format(
                afk_chan=bold(str(guild.afk_channel)) if guild.afk_channel else bold("Not set"),
                afk_timeout=bold(humanize_timedelta(seconds=guild.afk_timeout)),
                emoji_count=bold(humanize_number(len(guild.emojis))),
                role_count=bold(humanize_number(len(guild.roles))),
            ),
            inline=False,
        )
        if guild_features_list:
            data.add_field(name="Server features:", value="\n".join(guild_features_list))
        if guild.premium_tier != 0:
            nitro_boost = (
                "Tier {boostlevel} with {nitroboosters} boosts\nFile size limit: {filelimit}\nEmoji limit: {emojis_limit}\nVCs max bitrate: {bitrate}"
            ).format(
                boostlevel=bold(str(guild.premium_tier)),
                nitroboosters=bold(humanize_number(guild.premium_subscription_count)),
                filelimit=bold(_size(guild.filesize_limit)),
                emojis_limit=bold(str(guild.emoji_limit)),
                bitrate=bold(_bitsize(guild.bitrate_limit)),
            )
            data.add_field(name="Nitro Boost:", value=nitro_boost)
        if guild.splash:
            data.set_image(url=guild.splash_url_as(format="png"))
        data.set_footer(text=joined_on)

        await ctx.send(embed=data)

    @commands.command(aliases=["sav"])
    @commands.guild_only()
    async def serverav(self, ctx, user: discord.Member = None):
        # sourcery skip: avoid-builtin-shadow
        """Load a user's server avatar."""
        if not user:
            user = ctx.author

        member_route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=ctx.guild.id, member_id=user.id)

        async with ctx.typing():
            member_data = await ctx.cog.bot.http.request(member_route)
            member_av = member_data.get("avatar")
            embed = discord.Embed(description=f"{user.display_name}'s server av")
            if not member_av:
                return await ctx.send("Looks like that user doesn't have a server avatar set.")
            animated = member_av.startswith("a_")
            format = "gif" if animated else "png"
            av_url = f"https://cdn.discordapp.com/guilds/{ctx.guild.id}/users/{user.id}/avatars/{member_av}.{format}?size=1024"

            embed.set_image(url=av_url)
            return await ctx.send(embed=embed)

    @commands.command()
    @commands.max_concurrency(1, commands.BucketType.user)
    async def colorid(self, ctx, *, color: discord.Color) -> None:
        """Shows some info about provided color."""
        colorrgb = color.to_rgb()
        rgb_coords = [x / 255 for x in colorrgb]
        colorhsv = rgb_to_hsv(*colorrgb)
        h, l, s = colorsys.rgb_to_hls(*rgb_coords)
        colorhls = (colorhsv[0], l * 100, s * 100)
        coloryiq = colorsys.rgb_to_yiq(*rgb_coords)
        colorcmyk = rgb_to_cmyk(*colorrgb)
        colors_text = f"`HEX :` {str(color)}\n`RGB :` {colorrgb}\n`CMYK:` {tuple(isinstance(x, float) and round(x, 2) or x for x in colorcmyk)}\n`HSV :` {tuple(isinstance(x, float) and round(x, 2) or x for x in colorhsv)}\n`HLS :` {tuple(isinstance(x, float) and round(x, 2) or x for x in colorhls)}\n`YIQ :` {tuple(isinstance(x, float) and round(x, 2) or x for x in coloryiq)}\n`Int :` {color.value}"

        em = discord.Embed(
            title=str(color),
            description="`Name:` Loading...\n" + colors_text,
            url=f"http://www.color-hex.com/color/{str(color)[1:]}",
            colour=color,
            timestamp=ctx.message.created_at,
        )
        # CAUTION: That can fail soon
        em.set_thumbnail(url=f"https://api.alexflipnote.dev/color/image/{str(color)[1:]}")
        em.set_image(url=f"https://api.alexflipnote.dev/color/image/gradient/{str(color)[1:]}")
        m = await ctx.send(embed=em)

        async with self.bot.aio.get("https://www.thecolorapi.com/id", params={"hex": str(color)[1:]}) as data:
            color_response = await data.json(loads=json.loads)
            em.description = (
                f"`Name:` {color_response.get('name', {}).get('value', '?')} ({color_response.get('name', {}).get('closest_named_hex', '?')})\n{colors_text}"
            )

        await m.edit(embed=em)

    @commands.command(aliases=["fetchuser", "idlookup"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def getuserinfo(self, ctx, user_id: int) -> None:
        """Get info about any Discord's user by ID."""
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound as e:
            await ctx.send(chat.error(f"Discord user with ID `{user_id}` not found"))
            raise e
        except discord.HTTPException:
            await ctx.send(chat.warning(f"I was unable to get data about user with ID `{user_id}`. Try again later"))

            raise
        em = discord.Embed(title=chat.escape(str(user), formatting=True), timestamp=user.created_at, color=await ctx.embed_color())
        em.add_field(name="ID", value=user.id)
        em.add_field(name="Bot?", value=bool_emojify(user.bot))
        em.add_field(name="System?", value=bool_emojify(user.system))
        em.add_field(name="Mention", value=user.mention)
        em.add_field(name="Default avatar", value=f"[{user.default_avatar}]({user.default_avatar_url})")
        if user.avatar:
            em.add_field(name="Avatar", value=f"[`{user.avatar}`]({user.avatar_url_as(static_format='png', size=4096)})")
        if user.public_flags.value:
            em.add_field(name="Public flags", value="\n".join(str(flag)[10:].replace("_", " ").capitalize() for flag in user.public_flags.all()), inline=False)
        em.set_image(url=user.avatar_url_as(static_format="png", size=4096))
        em.set_thumbnail(url=user.default_avatar_url)
        em.set_footer(text="Created at")
        await ctx.send(embed=em)

    @commands.command(aliases=["activity", "activities"])
    @commands.guild_only()
    async def status(self, ctx, *, member: discord.Member = None) -> None:
        """List user's activities."""
        if member is None:
            member = ctx.message.author
        if not (activities := member.activities):
            await ctx.send(chat.info("Right now this user is doing nothing"))
            return
        await BaseMenu(ActivityPager(activities)).start(ctx)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def bans(self, ctx: commands.Context, *, server: commands.GuildConverter = None) -> None:
        """Get bans from server by id."""
        if server is None or not await self.bot.is_owner(ctx.author):
            server = ctx.guild
        if not server.me.guild_permissions.ban_members:
            await ctx.send('I need permission "Ban Members" to access banned members on server')
            return

        banlist = []
        async for b in BanIterator(ctx, ctx.guild):
            banlist.append(b)
        if banlist:
            banlist = sorted(banlist, key=lambda x: x.user.name.lower())
            banlisttext = "\n".join(f"{x.user} ({x.user.id})" for x in banlist)

            async def send_output() -> None:
                t = create_task(BaseMenu(PagePager(list(chat.pagify(banlisttext)))).start(ctx))
                await asyncio.sleep(0.4)
                await ctx.send(f"{len(banlist)} total bans")
                await t

            await send_output()

        else:
            await ctx.send("Banlist is empty!")

    @commands.command(aliases=["chaninfo", "channelinfo"])
    @commands.guild_only()
    async def cinfo(self, ctx, *, channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel] = None) -> None:
        """Get info about channel."""
        if channel is None:
            channel = ctx.channel
        changed_roles = sorted(channel.changed_roles, key=lambda r: r.position, reverse=True)
        em = discord.Embed(
            title=chat.escape(str(channel.name), formatting=True),
            description=(
                channel.topic
                if (topic := getattr(channel, "topic", None))
                else (
                    f"💬: {len(channel.text_channels)} | 🔈: {len(channel.voice_channels)} | 📡: {len(channel.stage_channels)}"
                    if isinstance(channel, discord.CategoryChannel)
                    else discord.Embed.Empty
                )
            ),
            color=await ctx.embed_color(),
        )

        em.add_field(name="ID", value=channel.id)
        em.add_field(name="Type", value=CHANNEL_TYPE_EMOJIS.get(channel.type, str(channel.type)))
        em.add_field(name="Exists since", value=channel.created_at.strftime(self.TIME_FORMAT))
        em.add_field(name="Category", value=chat.escape(str(channel.category), formatting=True) or chat.inline("Not in category"))
        em.add_field(name="Position", value=channel.position)
        if isinstance(channel, discord.TextChannel):
            em.add_field(name="Users", value=str(len(channel.members)))
        em.add_field(name="Changed roles permissions", value=chat.escape("\n".join(str(x) for x in changed_roles) or ("Not set"), formatting=True))
        em.add_field(name="Mention", value=f"{channel.mention}\n{chat.inline(channel.mention)}")
        if isinstance(channel, discord.TextChannel):
            if channel.slowmode_delay:
                em.add_field(name="Slowmode delay", value=f"{channel.slowmode_delay} seconds")
            em.add_field(name="NSFW", value=bool_emojify(channel.is_nsfw()))
            if channel.guild.me.permissions_in(channel).manage_webhooks and await channel.webhooks():
                em.add_field(name="Webhooks count", value=str(len(await channel.webhooks())))
        elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            em.add_field(name="Region", value=channel.rtc_region or ("Automatic"))
            em.add_field(name="Bitrate", value=f"{channel.bitrate / 1000}kbps")
            em.add_field(name="Users", value=channel.user_limit and f"{len(channel.members)}/{channel.user_limit}" or f"{len(channel.members)}")
            if isinstance(channel, discord.StageChannel):
                em.add_field(name="Requesting to speak", value=f"{len(channel.requesting_to_speak)} users")

        elif isinstance(channel, discord.CategoryChannel):
            em.add_field(name="NSFW", value=bool_emojify(channel.is_nsfw()))
        await ctx.send(embed=em)

    @commands.command(aliases=["calc"])
    async def calculate(self, ctx, *, query) -> None:
        """Math."""
        query = query.replace(",", "")
        engine_input = "{m:" + query + "}"
        start = time.monotonic()
        output = self.engine.process(engine_input)
        end = time.monotonic()

        output_string = output.body.replace("{m:", "").replace("}", "")
        e = discord.Embed(color=await ctx.embed_color(), title=f"Input: `{query}`", description=f"Output: `{output_string}`")
        e.set_footer(text=f"Calculated in {round((end - start) * 1000, 3)} ms")
        await ctx.send(embed=e)

    @commands.command(aliases=["cperms"])
    @commands.guild_only()
    @checks.has_permissions(administrator=True)
    async def chanperms(
        self,
        ctx,
        member: Optional[discord.Member],
        *,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel] = None,
    ) -> None:
        """Check user's permission for current or provided channel."""
        if not member:
            member = ctx.author
        if not channel:
            channel = ctx.channel
        perms = channel.permissions_for(member)
        await ctx.send(f'{chat.inline(str(perms.value))}\n{chat.box(chat.format_perms_list(perms) if perms.value else ("No permissions"), lang="py")}')

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(manage_guild=True)
    async def restartvoice(self, ctx) -> None:
        """Change server's voice region to random and back.

        Useful to reinitate all voice connections

        """
        current_region = ctx.guild.region
        random_region = choice([r for r in discord.VoiceRegion if not r.value.startswith("vip") and current_region != r])
        await ctx.guild.edit(region=random_region)
        await ctx.guild.edit(region=current_region, reason=get_audit_reason(ctx.author, "Voice restart"))
        await ctx.tick()

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(3, 45, commands.BucketType.guild)
    @checks.has_permissions(move_members=True)
    @commands.bot_has_guild_permissions(move_members=True)
    async def massmove(self, ctx, from_channel: discord.VoiceChannel, to_channel: discord.VoiceChannel = None) -> None:
        """Move all members from one voice channel to another.

        Use double quotes if channel name has spaces

        """
        fails = 0
        if not from_channel.members:
            await ctx.send(chat.error(f"There is no users in channel {from_channel.mention}."))

            return
        if not from_channel.permissions_for(ctx.me).move_members:
            await ctx.send(chat.error("I cant move users from that channel"))
            return
        if to_channel and not to_channel.permissions_for(ctx.me).connect:
            await ctx.send(chat.error("I cant move users to that channel"))
            return
        async with ctx.typing():
            for member in from_channel.members:
                try:
                    await member.move_to(to_channel, reason=get_audit_reason(ctx.author, "Massmove"))
                except discord.HTTPException:
                    fails += 1
                    continue
        await ctx.send(f"Finished moving users. {fails} members could not be moved.")

    @commands.guild_only()
    @commands.command()
    @checks.has_permissions(manage_guild=True)
    async def perms(self, ctx, user: discord.Member = None) -> None:
        """Fetch a specific user's permissions."""
        if user is None:
            user = ctx.author

        perms = iter(ctx.channel.permissions_for(user))
        perms_we_have = ""
        perms_we_dont = ""
        for x in sorted(perms):
            if "True" in str(x):
                perms_we_have += f"""+\t{str(x).split("'")[1]}\n"""
            else:
                perms_we_dont += f"""-\t{str(x).split("'")[1]}\n"""
        await ctx.send(cf.box(f"{perms_we_have}{perms_we_dont}", lang="diff"))

    @commands.guild_only()
    @commands.command()
    async def chinfo(self, ctx, channel: int = None):
        """Shows channel information.

        Defaults to current text channel.

        """
        channel = ctx.channel if channel is None else self.bot.get_channel(channel)
        if channel is None:
            return await ctx.send("Not a valid channel.")

        if channel:
            channel.guild

        yesno = {True: "Yes", False: "No"}
        typemap = {discord.TextChannel: "Text Channel", discord.VoiceChannel: "Voice Channel", discord.CategoryChannel: "Category"}

        load = "```\nLoading channel info...```"
        waiting = await ctx.send(load)

        with sps(Exception):
            caller = inspect.currentframe().f_back.f_code.co_name.strip()

        data = "```ini\n"
        if caller == "invoke" or channel.guild != ctx.guild:
            data += f"[Server]:     {channel.guild.name}\n"
        data += f"[Name]:       {cf.escape(str(channel))}\n"
        data += f"[ID]:         {channel.id}\n"
        data += f"[Private]:    {yesno[isinstance(channel, discord.abc.PrivateChannel)]}\n"

        if isinstance(channel, discord.TextChannel) and channel.topic != "":
            data += f"[Topic]:      {channel.topic}\n"
        data += f"[Position]:   {channel.position}\n"
        data += f"[Created]:    {dynamic_time(channel.created_at)}\n"
        data += f"[Type]:       {typemap[type(channel)]}\n"
        if isinstance(channel, discord.VoiceChannel):
            data += f"[Users]:      {len(channel.members)}\n"
            data += f"[User limit]: {channel.user_limit}\n"
            data += f"[Bitrate]:    {int(channel.bitrate / 1000)}kbps\n"
        data += "```"
        await asyncio.sleep(1)
        await waiting.edit(content=data)

    @commands.guild_only()
    @commands.command(aliases=["listroles"])
    async def roles(self, ctx) -> None:
        """Displays the server's roles."""
        form = "`{rpos:0{zpadding}}` - `{rid}` - `{rcolor}` - {rment} "
        max_zpadding = max(len(str(r.position)) for r in ctx.guild.roles)
        rolelist = [form.format(rpos=r.position, zpadding=max_zpadding, rid=r.id, rment=r.mention, rcolor=r.color) for r in ctx.guild.roles]

        rolelist = sorted(rolelist, reverse=True)
        rolelist = "\n".join(rolelist)
        embed_list = []
        for page in cf.pagify(rolelist, shorten_by=1400):
            embed = discord.Embed(description=f"**Total roles:** {len(ctx.guild.roles)}\n\n{page}", colour=await ctx.embed_colour())
            embed_list.append(embed)
        await menu(ctx, embed_list, DEFAULT_CONTROLS)

    @commands.guild_only()
    @commands.command(aliases=["rolemembers", "inrole"])
    @checks.has_permissions(manage_roles=True)
    async def who(self, ctx, *, role: discord.Role):  # sourcery no-metrics
        """Check members in the role specified."""
        guild = ctx.guild

        awaiter = await ctx.send(embed=discord.Embed(description="Getting member names...", colour=await ctx.embed_colour()))
        await asyncio.sleep(0.2)  # taking time to retrieve the names
        users_in_role = "\n".join(sorted(f"{m.mention} ({m.id})" for m in guild.members if role in m.roles))
        if not users_in_role:
            embed = discord.Embed(description=cf.bold(f"0 users found in the {role.name} role."), colour=await ctx.embed_colour())
            await awaiter.edit(embed=embed)
            return
        with sps(discord.NotFound):
            await awaiter.delete()
        embed_list = []
        for page in cf.pagify(users_in_role, delims=["\n"], page_length=1900):
            embed = discord.Embed(
                title=cf.bold("{1} users found in the {0} role.\n").format(role.name, len([m for m in guild.members if role in m.roles])),
                colour=await ctx.embed_colour(),
            )

            embed.description = page
            embed_list.append(embed)
        final_embed_list = []
        for i, embed in enumerate(embed_list):
            embed: discord.Embed
            embed.set_footer(text=f"melanie | Page {i + 1}/{len(embed_list)}", icon_url=footer_gif)
            final_embed_list.append(embed)
        if len(embed_list) == 1:
            close_control = {"\N{CROSS MARK}": close_menu}
            await menu(ctx, final_embed_list, close_control)
        else:
            await menu(ctx, final_embed_list, DEFAULT_CONTROLS)

    @commands.command(aliases=["roleinfo"])
    @commands.guild_only()
    async def rinfo(self, ctx, *, role: discord.Role) -> None:
        """Get info about role."""
        em = discord.Embed(title=chat.escape(role.name, formatting=True), color=role.color if role.color.value else discord.Embed.Empty)
        em.add_field(name="ID", value=role.id)
        em.add_field(name="Permissions", value=f"[{role.permissions.value}](https://cogs.fixator10.ru/permissions-calculator/?v={role.permissions.value})")
        em.add_field(name="Exists since", value=role.created_at.strftime(self.TIME_FORMAT))
        em.add_field(name="Color", value=role.colour)
        em.add_field(name="Members", value=str(len(role.members)))
        em.add_field(name="Position", value=role.position)
        em.add_field(name="Managed", value=bool_emojify(role.managed))
        em.add_field(name="Managed by bot", value=bool_emojify(role.is_bot_managed()))
        em.add_field(name="Managed by boosts", value=bool_emojify(role.is_premium_subscriber()))
        em.add_field(name="Managed by integration", value=bool_emojify(role.is_integration()))
        em.add_field(name="Hoist", value=bool_emojify(role.hoist))
        em.add_field(name="Mentionable", value=bool_emojify(role.mentionable))
        em.add_field(name="Mention", value=role.mention + "\n`" + role.mention + "`")
        em.set_thumbnail(url=f"https://xenforo.com/community/rgba.php?r={role.colour.r}&g={role.color.g}&b={role.color.b}&a=255")
        await ctx.send(embed=em)

    @commands.guild_only()
    @commands.command()
    async def joined(self, ctx, user: discord.Member = None) -> None:
        """Show when a user joined the guild."""
        if not user:
            user = ctx.author
        if user.joined_at:
            user_joined = user.joined_at.strftime("%d %b %Y %H:%M")
            since_joined = (ctx.message.created_at - user.joined_at).days
            joined_on = f"{user_joined} ({since_joined} days ago)"
        else:
            joined_on = "a mysterious date that not even Discord knows."

        if ctx.channel.permissions_for(ctx.guild.me).embed_links:
            embed = discord.Embed(description=f"{user.mention} joined this guild on {joined_on}.", color=await ctx.embed_colour())
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{user.display_name} joined this guild on {joined_on}.")

    @commands.guild_only()
    @checks.has_permissions(manage_channels=True)
    @commands.command(name="listchannel", aliases=["channellist"])
    async def listchannel(self, ctx) -> None:
        """List the channels of the current server."""

        def asciidoc(m):
            return f"```asciidoc\n{m}\n```"

        channels = ctx.guild.channels
        top_channels, category_channels = sort_channels(ctx.guild.channels)

        topChannels_formed = "\n".join(channels_format(top_channels))
        categories_formed = "\n\n".join(category_format(tup) for tup in category_channels)

        await ctx.send(f"{ctx.guild.name} has {len(channels)} channel{'s' if len(channels) > 1 else ''}.")

        for page in cf.pagify(topChannels_formed, delims=["\n"], shorten_by=16):
            await ctx.send(asciidoc(page))

        for page in cf.pagify(categories_formed, delims=["\n\n"], shorten_by=16):
            await ctx.send(asciidoc(page))

    @commands.guild_only()
    @commands.command()
    @checks.has_permissions(manage_roles=True)
    async def newusers(self, ctx, count: int = 10, fm: str = "py") -> None:
        """Lists the newest 5 members."""
        guild = ctx.guild
        count = max(min(count, 25), 5)
        members = sorted(guild.members, key=lambda m: m.joined_at, reverse=True)[:count]

        head1 = f"{count} newest members"
        header = f"{head1:>33}\n{'-' * 57}\n\n"

        user_body = " {mem} ({memid})\n {spcs}Joined Guild:    {sp1}{join}\n {spcs}Account Created: {sp2}{created}\n\n"

        disp = header
        spcs = [" " * (len(m.name) // 2) for m in members]
        smspc = min(spcs, key=lambda it: len(it))

        def calculate_diff(date1, date2):
            date1str, date2str = dynamic_time(date1), dynamic_time(date2)
            date1sta, date2sta = date1str.split(" ")[0], date2str.split(" ")[0]

            if len(date1sta) == len(date2sta):
                return (0, 0)
            ret = len(date2sta) - len(date1sta)
            return (abs(ret), 0 if ret > 0 else 1)

        for member in members:
            req = calculate_diff(member.joined_at, member.created_at)
            sp1 = req[0] if req[1] == 0 else 0
            sp2 = req[0] if req[1] == 1 else 0

            disp += user_body.format(
                mem=member.display_name,
                memid=member.id,
                join=dynamic_time(member.joined_at),
                created=dynamic_time(member.created_at),
                spcs=smspc,
                sp1="0" * sp1,
                sp2="0" * sp2,
            )

        for page in cf.pagify(disp, delims=["\n\n"]):
            await ctx.send(cf.box(page, lang=fm))

    @commands.command()
    async def firstmessage(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Provide a link to the first message in current or provided channel."""
        if channel is None:
            channel = ctx.channel
        try:
            async with ctx.typing():
                message: discord.Message = (await channel.history(limit=1, oldest_first=True).flatten())[0]
        except (discord.Forbidden, discord.HTTPException):
            await ctx.maybe_send_embed("Unable to read message history for that channel")
            return

        await ctx.send(message.jump_url)

    async def gen_emojis(self) -> None:
        config = await self.config.all()
        self.status_emojis = {
            "mobile": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["mobile"]),
            "online": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["online"]),
            "away": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["away"]),
            "dnd": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["dnd"]),
            "offline": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["offline"]),
            "streaming": discord.utils.get(self.bot.emojis, id=config["status_emojis"]["streaming"]),
        }
        self.badge_emojis = {
            "staff": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["staff"]),
            "early_supporter": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["early_supporter"]),
            "hypesquad_balance": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["hypesquad_balance"]),
            "hypesquad_bravery": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["hypesquad_bravery"]),
            "hypesquad_brilliance": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["hypesquad_brilliance"]),
            "hypesquad": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["hypesquad"]),
            "verified_bot_developer": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["verified_bot_developer"]),
            "bug_hunter": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["bug_hunter"]),
            "bug_hunter_level_2": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["bug_hunter_level_2"]),
            "partner": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["partner"]),
            "verified_bot": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["verified_bot"]),
            "verified_bot2": discord.utils.get(self.bot.emojis, id=config["badge_emojis"]["verified_bot2"]),
        }
