from __future__ import annotations

import asyncio
import datetime
from contextlib import suppress
from io import BytesIO
from typing import TYPE_CHECKING, Optional, Union

import arrow
import asyncpg
import discord
import yarl
from discord.embeds import color_tasks
from loguru import logger as log
from melaniebot import VersionInfo, version_info
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import (
    bold,
    escape,
    humanize_list,
    humanize_number,
    humanize_timedelta,
    pagify,
)
from melaniebot.core.utils.predicates import MessagePredicate
from starlette.responses import guess_type
from tornado.curl_httpclient import CurlAsyncHTTPClient
from yarl import URL

from melanie import global_curl, intcomma, make_e
from notsobot.converter import ImageFinder

from .converters import (
    ChannelConverter,
    GuildConverter,
    MultiGuildConverter,
    PermissionConverter,
)
from .menus import AvatarPages, BaseMenu, GuildPages, ListPages

if TYPE_CHECKING:
    from executionstracker.exe import ExecutionsTracker


def _(x):
    return x


lookup = """select count(*) total_cmds, last(created_at, created_at) last_cmd, guild_id
from executions
group by guild_id
order by last_cmd desc"""


class ServerStats(commands.Cog):
    """Gather useful information about servers."""

    def __init__(self, bot) -> None:
        self.bot: Melanie = bot
        default_global: dict = {"join_channel": None}
        default_guild: dict = {"last_checked": 0, "members": {}, "total": 0, "channels": {}}
        self.config: Config = Config.get_conf(self, 54853421465543, force_registration=True)
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)

    @checks.is_owner()
    @commands.command(hidden=True)
    async def topservers(self, ctx: commands.Context) -> None:
        """Lists servers by number of users and shows number of users."""
        msg = ""

        exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")

        async with ctx.typing():
            if exe:
                async with exe.database.acquire() as con:
                    con: asyncpg.Connection
                    async with con.transaction():
                        async for record in con.cursor(lookup):
                            if server := self.bot.get_guild(int(record["guild_id"])):
                                cnt = record["total_cmds"]
                                last = record["last_cmd"]
                                if last:
                                    last = int(arrow.get(last).timestamp())
                                    last = f"<t:{last}:R>"
                                ts = int(server.me.joined_at.timestamp())
                                msg += f"{escape(server.name, mass_mentions=True, formatting=True)} ({server.id}): `{humanize_number(server.member_count)}` T: {intcomma(cnt)} J: <t:{ts}:R> L: {last} \n\n"
            msg_list = []
            for page in pagify(msg, delims=["\n"], page_length=1000):
                msg_list.append(discord.Embed(colour=await self.bot.get_embed_colour(ctx.channel), description=page))

            await BaseMenu(source=ListPages(pages=msg_list), cog=self).start(ctx=ctx)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx: commands.Context, member: Optional[discord.User]):
        """Display a users avatar in chat."""
        async with asyncio.timeout(20):
            if not member:
                member = ctx.author

            await BaseMenu(
                source=AvatarPages(members=[member], dask_client=self.bot.dask),
                delete_message_after=False,
                clear_reactions_after=False,
                timeout=120,
                cog=self,
            ).start(ctx=ctx)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Build and send a message containing serverinfo when the bot joins a new
        server.
        """
        if baron := self.bot.get_cog("Baron"):
            whitelist = await baron.config.whitelist()
            if guild.id not in whitelist:
                return log.warning(f"A join attempt was made @ {guild} / {guild.id} but it is not whitelisted")

        channel_id = await self.config.join_channel()
        if channel_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        passed = (datetime.datetime.utcnow() - guild.created_at).days
        created_at = (
            "{bot} has joined a server!\n That's **{num}** servers now!\nThat's a total of **{users}** users !\nServer created on **{since}**. That's over **{passed}** days ago!"
        ).format(
            bot=channel.guild.me.mention,
            num=humanize_number(len(self.bot.guilds)),
            users=humanize_number(len(self.bot.users)),
            since=guild.created_at.strftime("%d %b %Y %H:%M:%S"),
            passed=passed,
        )
        try:
            em = await self.guild_embed(guild)
            em.description = created_at
            await channel.send(embed=em)
        except Exception:
            log.exception(f"Error creating guild embed for new guild ID {guild.id}")

    async def guild_embed(self, guild: discord.Guild) -> discord.Embed:
        """Builds the guild embed information used throughout the cog."""

        def _size(num) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1024.0:
                    return f"{num:.1f}{unit}"
                num /= 1024.0
            return f"{num:.1f}{'YB'}"

        def _bitsize(num) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1000.0:
                    return f"{num:.1f}{unit}"
                num /= 1000.0
            return f"{num:.1f}{'YB'}"

        passed = (datetime.datetime.utcnow() - guild.created_at).days
        created_at = ("Created on {date}. That's over {num} days ago!").format(
            date=bold(guild.created_at.strftime("%d %b %Y %H:%M")),
            num=bold(humanize_number(passed)),
        )

        total_users = humanize_number(guild.member_count)
        try:
            joined_at = guild.me.joined_at
        except AttributeError:
            joined_at = datetime.datetime.now(datetime.timezone.utc)
        bot_joined = joined_at.strftime("%d %b %Y %H:%M:%S")
        since_joined = (datetime.datetime.utcnow() - joined_at).days
        joined_on = ("**{bot_name}** joined this server on **{bot_join}**.\nThat's over **{since_join}** days ago!").format(
            bot_name=self.bot.user.name,
            bot_join=bot_joined,
            since_join=since_joined,
        )
        shard = ""
        colour = guild.roles[-1].colour
        online_stats = {
            "Humans: ": lambda x: not x.bot,
            " • Bots: ": lambda x: x.bot,
            "\N{LARGE GREEN CIRCLE}": lambda x: x.status is discord.Status.online,
            "\N{LARGE ORANGE CIRCLE}": lambda x: x.status is discord.Status.idle,
            "\N{LARGE RED CIRCLE}": lambda x: x.status is discord.Status.do_not_disturb,
            "\N{MEDIUM WHITE CIRCLE}": lambda x: x.status is discord.Status.offline,
            "\N{LARGE PURPLE CIRCLE}": lambda x: x.activity is not None and x.activity.type is discord.ActivityType.streaming,
        }

        member_msg = ("Total Users: {}\n").format(bold(total_users))
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
        text_channels = len(guild.text_channels)
        nsfw_channels = len([c for c in guild.text_channels if c.is_nsfw()])
        voice_channels = len(guild.voice_channels)
        vc_regions = {
            "vip-us-east": "__VIP__ US East " + "\U0001f1fa\U0001f1f8",
            "vip-us-west": "__VIP__ US West " + "\U0001f1fa\U0001f1f8",
            "vip-amsterdam": "__VIP__ Amsterdam " + "\U0001f1f3\U0001f1f1",
            "eu-west": "EU West " + "\U0001f1ea\U0001f1fa",
            "eu-central": "EU Central " + "\U0001f1ea\U0001f1fa",
            "europe": "Europe " + "\U0001f1ea\U0001f1fa",
            "london": "London " + "\U0001f1ec\U0001f1e7",
            "frankfurt": "Frankfurt " + "\U0001f1e9\U0001f1ea",
            "amsterdam": "Amsterdam " + "\U0001f1f3\U0001f1f1",
            "us-west": "US West " + "\U0001f1fa\U0001f1f8",
            "us-east": "US East " + "\U0001f1fa\U0001f1f8",
            "us-south": "US South " + "\U0001f1fa\U0001f1f8",
            "us-central": "US Central " + "\U0001f1fa\U0001f1f8",
            "singapore": "Singapore " + "\U0001f1f8\U0001f1ec",
            "sydney": "Sydney " + "\U0001f1e6\U0001f1fa",
            "brazil": "Brazil " + "\U0001f1e7\U0001f1f7",
            "hongkong": "Hong Kong " + "\U0001f1ed\U0001f1f0",
            "russia": "Russia " + "\U0001f1f7\U0001f1fa",
            "japan": "Japan " + "\U0001f1ef\U0001f1f5",
            "southafrica": "South Africa " + "\U0001f1ff\U0001f1e6",
            "india": "India " + "\U0001f1ee\U0001f1f3",
            "south-korea": "South Korea " + "\U0001f1f0\U0001f1f7",
        }

        verif = {"none": "0 - None", "low": "1 - Low", "medium": "2 - Medium", "high": "3 - High", "extreme": "4 - Extreme"}

        features = {
            "ANIMATED_ICON": "Animated Icon",
            "BANNER": "Banner Image",
            "COMMERCE": "Commerce",
            "COMMUNITY": "Community",
            "DISCOVERABLE": "Server Discovery",
            "FEATURABLE": "Featurable",
            "INVITE_SPLASH": "Splash Invite",
            "MEMBER_LIST_DISABLED": "Member list disabled",
            "MEMBER_VERIFICATION_GATE_ENABLED": "Membership Screening enabled",
            "MORE_EMOJI": "More Emojis",
            "NEWS": "News Channels",
            "PARTNERED": "Partnered",
            "PREVIEW_ENABLED": "Preview enabled",
            "PUBLIC_DISABLED": "Public disabled",
            "VANITY_URL": "Vanity URL",
            "VERIFIED": "Verified",
            "VIP_REGIONS": "VIP Voice Servers",
            "WELCOME_SCREEN_ENABLED": "Welcome Screen enabled",
        }

        guild_features_list = [f"✅ {name}" for feature, name in features.items() if feature in guild.features]

        em = discord.Embed(description=(f"{guild.description}\n\n" if guild.description else "") + f"{created_at}\n{joined_on}", colour=colour)

        em.set_author(
            name=guild.name,
            icon_url=(
                "https://cdn.discordapp.com/emojis/457879292152381443.png"
                if "VERIFIED" in guild.features
                else "https://cdn.discordapp.com/emojis/508929941610430464.png"
                if "PARTNERED" in guild.features
                else discord.Embed.Empty
            ),
            url=guild.icon_url or "https://cdn.discordapp.com/embed/avatars/1.png",
        )

        em.set_thumbnail(url=guild.icon_url or "https://cdn.discordapp.com/embed/avatars/1.png")

        em.add_field(name="Members:", value=member_msg)
        em.add_field(
            name="Channels:",
            value=("\N{SPEECH BALLOON} Text: {text}\n{nsfw}\N{SPEAKER WITH THREE SOUND WAVES} Voice: {voice}").format(
                text=bold(humanize_number(text_channels)),
                nsfw=("\N{NO ONE UNDER EIGHTEEN SYMBOL} Nsfw: {}\n").format(bold(humanize_number(nsfw_channels))) if nsfw_channels else "",
                voice=bold(humanize_number(voice_channels)),
            ),
        )

        owner = guild.owner or await self.bot.get_or_fetch_user(guild.owner_id)
        em.add_field(
            name="Utility:",
            value=("Owner: {owner_mention}\n{owner}\nRegion: {region}\nVerif. level: {verif}\nServer ID: {id}{shard}").format(
                owner_mention=bold(str(owner.mention)),
                owner=bold(str(owner)),
                region=f"**{vc_regions.get(str(guild.region)) or str(guild.region)}**",
                verif=bold(verif[str(guild.verification_level)]),
                id=bold(str(guild.id)),
                shard=shard,
            ),
            inline=False,
        )

        em.add_field(
            name="Misc:",
            value=("AFK channel: {afk_chan}\nAFK timeout: {afk_timeout}\nCustom emojis: {emojis}\nRoles: {roles}").format(
                afk_chan=bold(str(guild.afk_channel)) if guild.afk_channel else bold("Not set"),
                afk_timeout=bold(humanize_timedelta(seconds=guild.afk_timeout)),
                emojis=bold(humanize_number(len(guild.emojis))),
                roles=bold(humanize_number(len(guild.roles))),
            ),
            inline=False,
        )

        if guild_features_list:
            em.add_field(name="Server features:", value="\n".join(guild_features_list))
        if guild.premium_tier != 0:
            nitro_boost = (
                "Tier {boostlevel} with {nitroboosters} boosters\nFile size limit: {filelimit}\nEmoji limit: {emojis_limit}\nVCs max bitrate: {bitrate}"
            ).format(
                boostlevel=bold(str(guild.premium_tier)),
                nitroboosters=bold(humanize_number(guild.premium_subscription_count)),
                filelimit=bold(_size(guild.filesize_limit)),
                emojis_limit=bold(str(guild.emoji_limit)),
                bitrate=bold(_bitsize(guild.bitrate_limit)),
            )

            em.add_field(name="Nitro Boost:", value=nitro_boost)
        if guild.splash:
            em.set_image(url=guild.banner_url_as(format="png"))
        return em

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Build and send a message containing serverinfo when the bot leaves a
        server.
        """
        if baron := self.bot.get_cog("Baron"):
            whitelist = await baron.config.whitelist()
            if guild.id not in whitelist:
                return log.warning(f"Left an unwhitelisted guild {guild} / {guild.id}")

        channel_id = await self.config.join_channel()
        if channel_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        passed = (datetime.datetime.utcnow() - guild.created_at).days
        bot_fmt = channel.guild.me.mention if hasattr(channel, "guild") else None
        created_at = (
            "{bot} has left a server!\n That's **{num}** servers now!\nThat's a total of **{users}** users !\nServer created on **{since}**. That's over **{passed}** days ago!"
        ).format(
            bot=bot_fmt,
            num=humanize_number(len(self.bot.guilds)),
            users=humanize_number(len(self.bot.users)),
            since=guild.created_at.strftime("%d %b %Y %H:%M"),
            passed=passed,
        )
        try:
            em = await self.guild_embed(guild)
            em.description = created_at
            await channel.send(embed=em)
        except Exception:
            log.info(f"Error creating guild embed for old guild ID {guild.id} - Possibly discord termination")

    @commands.command()
    @checks.mod_or_permissions(manage_channels=True)
    async def topic(self, ctx: commands.Context, channel: Optional[discord.TextChannel], *, topic: str = "") -> None:
        """Sets a specified channels topic.

        `channel` is optional and if not supplied will use the current channel
        Note: The maximum number of characters is 1024

        """
        if channel is None:
            channel = ctx.channel
        if not channel.permissions_for(ctx.author).manage_messages:
            return
        if not channel.permissions_for(ctx.me).manage_channels:
            await ctx.send(_('I require the "Manage Channels" permission to execute that command.'))
            return
        await channel.edit(topic=topic[:1024], reason=("Requested by {author}").format(author=ctx.author))
        await ctx.tick()

    @commands.group()
    @checks.mod_or_permissions(manage_channels=True)
    async def channeledit(self, ctx: commands.Context) -> None:
        """Modify channel options."""

    @channeledit.command(name="name")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_name(self, ctx: commands.Context, channel: Optional[ChannelConverter], *, name: str) -> None:
        """Edit a channels name."""
        if not channel:
            channel = ctx.channel
        await channel.edit(name=name[:100], reason=("Requested by {author}").format(author=ctx.author))
        await ctx.tick()

    @channeledit.command(name="position")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_position(self, ctx: commands.Context, channel: Optional[ChannelConverter], position: int) -> None:
        """Edit a channels position."""
        if not channel:
            channel = ctx.channel
        try:
            await channel.edit(position=position, reason=("Requested by {author}").format(author=ctx.author))
        except Exception as e:
            log.info(e)
            return
        await ctx.tick()

    @channeledit.command(name="sync")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_sync(self, ctx: commands.Context, channel: Optional[ChannelConverter], toggle: bool) -> None:
        """Set whether or not to sync permissions with the channels Category."""
        if not channel:
            channel = ctx.channel
        await channel.edit(sync_permissions=toggle, reason=("Requested by {author}").format(author=ctx.author))
        await ctx.tick()

    @channeledit.command(name="nsfw")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_nsfw(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set whether or not a channel is NSFW."""
        if not channel:
            channel: discord.TextChannel = ctx.channel

        target_state = not channel.is_nsfw()
        await channel.edit(nsfw=target_state, reason=f"Requested by {ctx.author}")
        await ctx.tick()

    @channeledit.command(name="topic")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_topic(self, ctx: commands.Context, channel: Optional[discord.TextChannel], *, topic: str) -> None:
        """Edit a channels topic."""
        if not channel:
            channel = ctx.channel
        await channel.edit(topic=topic[:1024], reason=("Requested by {author}").format(author=ctx.author))
        await ctx.tick()

    @channeledit.command(name="bitrate")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_bitrate(self, ctx: commands.Context, channel: discord.VoiceChannel, bitrate: int) -> None:
        """Edit a voice channels bitrate."""
        try:
            await channel.edit(bitrate=bitrate, reason=("Requested by {author}").format(author=ctx.author))
        except Exception:
            await ctx.send(("`{bitrate}` is either too high or too low please provide a number between 8000 and 96000.").format(bitrate=bitrate))
            return
        await ctx.tick()

    @channeledit.command(name="userlimit")
    @checks.mod_or_permissions(manage_channels=True)
    async def channel_userlimit(self, ctx: commands.Context, channel: discord.VoiceChannel, limit: int) -> None:
        """Edit a voice channels user limit."""
        try:
            await channel.edit(user_limit=limit, reason=("Requested by {author}").format(author=ctx.author))
        except Exception:
            await ctx.send(("`{limit}` is either too high or too low please provide a number between 0 and 99.").format(limit=limit))
            return
        await ctx.tick()

    @channeledit.command(name="permissions", aliases=["perms", "permission"])
    @checks.mod_or_permissions(manage_permissions=True)
    async def edit_channel_perms(
        self,
        ctx: commands.Context,
        permission: PermissionConverter,
        channel: Optional[ChannelConverter],
        true_or_false: Optional[bool],
        *roles_or_users: Union[discord.Member, discord.Role, str],
    ) -> None:
        """Edit channel read permissions for designated role.

        `[channel]` The channel you would like to edit. If no channel is
        provided the channel this command is run in will be used.
        `[true_or_false]` `True` or `False` to set the permission level.
        If this is not provided `None` will be used instead which
        signifies the default state of the permission.
        `[roles_or_users]` the roles or users you want to edit this
        setting for.

        `<permission>` Must be one of the following:     add_reactions
        attach_files     connect     create_instant_invite
        deafen_members     embed_links     external_emojis
        manage_messages     manage_permissions     manage_roles
        manage_webhooks     move_members     mute_members
        priority_speaker     read_message_history     read_messages
        send_messages     send_tts_messages     speak     stream
        use_external_emojis     use_slash_commands use_voice_activation

        """
        if channel is None:
            channel = ctx.channel
        if not channel.permissions_for(ctx.author).manage_permissions or not channel.permissions_for(ctx.author).manage_channels:
            return await ctx.send(("You do not have the correct permissions to edit {channel}.").format(channel=channel.mention))
        if not channel.permissions_for(ctx.me).manage_permissions or not channel.permissions_for(ctx.author).manage_channels:
            return await ctx.send(("I do not have the correct permissions to edit {channel}.").format(channel=channel.mention))
        targets = list(roles_or_users)
        for r in roles_or_users:
            if isinstance(r, str):
                targets.remove(r)
                if r == "everyone":
                    targets.append(ctx.guild.default_role)
        if not targets:
            return await ctx.send("You need to provide a role or user you want to edit permissions for")
        overs = channel.overwrites
        for target in targets:
            if target in overs:
                overs[target].update(**{permission: true_or_false})

            else:
                perm = discord.PermissionOverwrite(**{permission: true_or_false})
                overs[target] = perm
        try:
            await channel.edit(overwrites=overs)
            await ctx.send(
                ("The following roles or users have had `{perm}` in {channel} set to `{perm_level}`:\n{roles_or_users}").format(
                    perm=permission,
                    channel=channel.mention,
                    perm_level=true_or_false,
                    roles_or_users=humanize_list([i.mention for i in targets]),
                ),
            )
        except Exception:
            log.exception(f"Error editing permissions in channel {channel.name}")
            return await ctx.send("There was an issue editing permissions on that channel.")

    async def ask_for_invite(self, ctx: commands.Context) -> Optional[str]:
        """Ask the user to provide an invite link if reinvite is True."""
        msg_send = "Please provide a reinvite link/message.\nType `exit` for no invite link/message."
        await ctx.send(msg_send)
        try:
            msg = await ctx.bot.wait_for("message", check=lambda m: m.author == ctx.message.author, timeout=30)
        except TimeoutError:
            await ctx.send("I Guess not.")
            return None
        return None if "exit" in msg.content else msg.content

    async def get_members_since(self, ctx: commands.Context, days: int, role: Union[discord.Role, tuple[discord.Role], None]) -> list[discord.Member]:
        now = datetime.datetime.now(datetime.timezone.utc)
        after = now - datetime.timedelta(days=days)
        member_list = []
        if role:
            if not isinstance(role, discord.Role):
                for r in role:
                    member_list.extend(m for m in r.members if m.top_role < ctx.me.top_role)
            else:
                member_list = [m for m in role.members if m.top_role < ctx.me.top_role]
        else:
            member_list = [m for m in ctx.guild.members if m.top_role < ctx.me.top_role]
        for channel in ctx.guild.text_channels:
            if not channel.permissions_for(ctx.me).read_message_history:
                continue
            async for message in channel.history(limit=None, after=after):
                if message.author in member_list:
                    member_list.remove(message.author)
        return member_list

    @commands.command()
    @checks.is_owner()
    async def setguildjoin(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Set a channel to see new servers the bot is joining."""
        if channel is None:
            channel = ctx.message.channel
        await self.config.join_channel.set(channel.id)
        msg = f"Posting new servers and left servers in {channel.mention}"
        await ctx.send(msg)

    @commands.command()
    @checks.is_owner()
    async def removeguildjoin(self, ctx: commands.Context) -> None:
        """Stop bots join/leave server messages."""
        await self.config.join_channel.set(None)
        await ctx.send("No longer posting joined or left servers.")

    @commands.command()
    async def whois(self, ctx: commands.Context, *, user_id: Union[int, discord.Member, discord.User] = None) -> None:
        """Display servers a user shares with the bot.

        `member` can be a user ID or mention

        """
        async with ctx.typing():
            if not user_id:
                user_id = ctx.author
            if isinstance(user_id, int):
                try:
                    member = await self.bot.fetch_user(user_id)
                except AttributeError:
                    member = await self.bot.get_user_info(user_id)
                except discord.errors.NotFound:
                    await ctx.send(f"{str(user_id)} doesn't seem to be a discord user.")
                    return
            else:
                member = user_id

            guild_list = []
            if await self.bot.is_owner(ctx.author):
                async for guild in AsyncIter(self.bot.guilds, steps=1):
                    if m := guild.get_member(member.id):
                        guild_list.append(m)
            else:
                async for guild in AsyncIter(self.bot.guilds, steps=1):
                    if not guild.get_member(ctx.author.id):
                        continue
                    if m := guild.get_member(member.id):
                        guild_list.append(m)
            embed_list = []
            robot = "\N{ROBOT FACE}" if member.bot else ""
            if guild_list != []:
                msg = f"**{member}** ({member.id}) {robot}" + "is on:\n\n"
                embed_msg = ""
                for m in guild_list:
                    is_owner = "\N{CROWN}" if m.id == m.guild.owner_id else ""
                    nick = f"`{m.nick}` in" if m.nick else ""
                    msg += f"{is_owner}{nick} __{m.guild.name}__ ({m.guild.id})\n\n"
                    embed_msg += f"{is_owner}{nick} __{m.guild.name}__ ({m.guild.id})\n\n"
                if ctx.channel.permissions_for(ctx.me).embed_links:
                    for em in pagify(embed_msg, ["\n"], page_length=6000):
                        embed = discord.Embed()
                        since_created = (ctx.message.created_at - member.created_at).days
                        user_created = member.created_at.strftime("%d %b %Y %H:%M")
                        public_flags = ""
                        if version_info >= VersionInfo.from_str("3.4.0"):
                            public_flags = "\n".join(bold(i.replace("_", " ").title()) for i, v in member.public_flags if v)
                        created_on = ("Joined Discord on {user_created}\n({since_created} days ago)\n{public_flags}").format(
                            user_created=user_created,
                            since_created=since_created,
                            public_flags=public_flags,
                        )
                        embed.description = created_on
                        embed.set_thumbnail(url=member.avatar_url)
                        embed.colour = await ctx.embed_colour()
                        embed.set_author(name=f"{member} ({member.id}) {robot}", icon_url=member.avatar_url)
                        for page in pagify(em, ["\n"], page_length=1024):
                            embed.add_field(name="Shared Servers", value=page)
                        embed_list.append(embed)
                else:
                    embed_list.extend(iter(pagify(msg, ["\n"])))
            elif ctx.channel.permissions_for(ctx.me).embed_links:
                embed = discord.Embed()
                since_created = (ctx.message.created_at - member.created_at).days
                user_created = member.created_at.strftime("%d %b %Y %H:%M")
                public_flags = ""
                if version_info >= VersionInfo.from_str("3.4.0"):
                    public_flags = "\n".join(bold(i.replace("_", " ").title()) for i, v in member.public_flags if v)
                created_on = ("Joined Discord on {user_created}\n({since_created} days ago)\n{public_flags}").format(
                    user_created=user_created,
                    since_created=since_created,
                    public_flags=public_flags,
                )
                embed.description = created_on
                embed.set_thumbnail(url=member.avatar_url)
                embed.colour = await ctx.embed_colour()
                embed.set_author(name=f"{member} ({member.id}) {robot}", icon_url=member.avatar_url)
                embed_list.append(embed)
            else:
                msg = f"**{member}** ({member.id}) is not in any shared servers!"
                embed_list.append(msg)
            await BaseMenu(
                source=ListPages(pages=embed_list),
                delete_message_after=False,
                clear_reactions_after=False,
                timeout=60,
                cog=self,
                page_start=0,
            ).start(ctx=ctx)

    @commands.group(aliases=["gedit", "sedit", "serveredit"])
    @checks.admin_or_permissions(manage_guild=True)
    async def guildedit(self, ctx: commands.Context) -> None:
        """Edit various guild settings."""

    @guildedit.command(name="icon")
    async def guild_icon(self, ctx, image: ImageFinder = None):
        """Set the icon of the server.

        `<image>` URL to the image or image uploaded with running the
        command

        """
        if image is None:
            image = await ImageFinder().search_for_images(ctx)

        url = image[0]

        b, mime = await self.bytes_download(url)
        if not b:
            return await ctx.send("That's not a valid image.")

        await ctx.guild.edit(icon=b.getvalue())
        return await ctx.tick()

    @guildedit.command(name="invite", aliases=["splash"])
    async def guild_invite(self, ctx, image: ImageFinder = None):
        """Set the invite splash screen of the server.

        `<image>` URL to the image or image uploaded with running the
        command

        """
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        url = image[0]

        b, mime = await self.bytes_download(url)
        if not b:
            return await ctx.send("That's not a valid image.")

        await ctx.guild.edit(splash=b.getvalue())
        return await ctx.tick()

    @guildedit.command(name="banner")
    async def guild_banner(self, ctx, image: ImageFinder = None):
        """Set the banner of the server.

        `<image>` URL to the image or image uploaded with running the
        command

        """
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        url = image[0]

        b, mime = await self.bytes_download(url)
        if not b:
            return await ctx.send("That's not a valid image.")

        await ctx.guild.edit(banner=b.getvalue())
        return await ctx.tick()

    @guildedit.command(name="name")
    async def guild_name(self, ctx: commands.Context, *, name: str):
        """Change the server name.

        `<name>` The new name of the server

        """
        reason = ("Requested by {author}").format(author=ctx.author)
        try:
            await ctx.guild.edit(name=name, reason=reason)
        except Exception:
            log.exception("Could not edit guild name")
            return await ctx.send("I could not edit the servers name.")
        await ctx.send(("Server name set to {name}.").format(name=name))

    @guildedit.command(name="verificationlevel", aliases=["verification"], hidden=True)
    async def verifivation_level(self, ctx: commands.Context, *, level: str) -> None:
        """Modify the guilds verification level.

        `<level>` must be one of: `none`, `low`, `medium`, `table
        flip`(`high`), or `double table flip`(`extreme`)

        """
        levels = {
            "none": discord.VerificationLevel.none,
            "low": discord.VerificationLevel.low,
            "medium": discord.VerificationLevel.medium,
            "high": discord.VerificationLevel.high,
            "table flip": discord.VerificationLevel.high,
            "extreme": discord.VerificationLevel.extreme,
            "double table flip": discord.VerificationLevel.extreme,
        }
        reason = ("Requested by {author}").format(author=ctx.author)
        if level.lower() not in levels:
            await ctx.send(f"`{level}` is not a proper verification level.")
            return
        try:
            await ctx.guild.edit(verification_level=levels[level], reason=reason)
        except Exception:
            log.exception("Could not edit guild verification level")
            return await ctx.send("I could not edit the servers verification level.")
        await ctx.send(("Server verification level set to {level}").format(level=level))

    @guildedit.command(name="systemchannel", aliases=["welcomechannel"], hidden=True)
    async def system_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Change the system channel.

        This is the default discord welcome channel. `[channel]` The
        channel you want to set as the system channel. If not provided
        will be set to `None`.

        """
        reason = ("Requested by {author}").format(author=ctx.author)
        try:
            await ctx.guild.edit(system_channel=channel, reason=reason)
        except Exception:
            log.exception("Could not edit guild systemchannel")
            return await ctx.send("I could not edit the servers systemchannel.")
        channel_name = getattr(channel, "mention", "None")
        await ctx.send(("Server systemchannel set to {channel}").format(channel=channel_name))

    @commands.command()
    @checks.is_owner()
    async def listchannels(self, ctx: commands.Context, *, guild: GuildConverter = None) -> None:
        """Lists channels and their position and ID for a server.

        `guild` can be either the server ID or name

        """
        if not guild:
            guild = ctx.guild
        msg = f"__**{guild.name}({guild.id})**__\n"
        for category in guild.by_category():
            if category[0] is not None:
                word = "Position"
                msg += f"{category[0].mention} ({category[0].id}): {word} {category[0].position}\n"
            word = "Position"
            for channel in category[1]:
                msg += f"{channel.mention} ({channel.id}): {word} {channel.position}\n"
        for page in pagify(msg, ["\n"]):
            await ctx.send(page)

    @staticmethod
    async def confirm_leave_guild(ctx: commands.Context, guild) -> None:
        await ctx.send(("Are you sure you want me to leave {guild}? (reply yes or no)").format(guild=guild.name))
        pred = MessagePredicate.yes_or_no(ctx)
        await ctx.bot.wait_for("message", check=pred)
        if pred.result is True:
            try:
                await ctx.send(("Leaving {guild}.").format(guild=guild.name))
                await guild.leave()
            except Exception:
                log.exception(("I couldn't leave {guild} ({g_id}).").format(guild=guild.name, g_id=guild.id))
                await ctx.send(("I couldn't leave {guild}.").format(guild=guild.name))
        else:
            await ctx.send(("Okay, not leaving {guild}.").format(guild=guild.name))

    @staticmethod
    async def get_guild_invite(guild: discord.Guild, max_age: int = 86400) -> None:
        """Handles the reinvite logic for getting an invite
        to send the newly unbanned user
        :returns: :class:`Invite`.


        """
        my_perms: discord.Permissions = guild.me.guild_permissions
        if my_perms.manage_guild or my_perms.administrator:
            if "VANITY_URL" in guild.features:
                # guild has a vanity url so use it as the one to send
                try:
                    return await guild.vanity_invite()
                except discord.errors.Forbidden:
                    invites = []
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

    @commands.command()
    async def getguild(self, ctx: commands.Context, *, guild: GuildConverter = None) -> None:
        """Display info about servers the bot is on.

        `guild_name` can be either the server ID or partial name

        """
        async with ctx.typing():
            if not ctx.guild and not await ctx.bot.is_owner(ctx.author):
                return await ctx.send("This command is not available in DM.")
            guilds = [ctx.guild]
            page = 0
            if await ctx.bot.is_owner(ctx.author):
                if ctx.guild:
                    page = ctx.bot.guilds.index(ctx.guild)
                guilds = ctx.bot.guilds
                if guild:
                    page = ctx.bot.guilds.index(guild)

        await BaseMenu(source=GuildPages(guilds=guilds), delete_message_after=False, clear_reactions_after=True, timeout=60, cog=self, page_start=page).start(
            ctx=ctx,
        )

    @commands.command()
    @checks.admin()
    async def getguilds(self, ctx: commands.Context, *, guilds: MultiGuildConverter) -> None:
        """Display info about multiple servers.

        `guild_name` can be either the server ID or partial name

        """
        async with ctx.typing():
            page = 0
            if not guilds:
                guilds = ctx.bot.guilds
                page = ctx.bot.guilds.index(ctx.guild)
        await BaseMenu(source=GuildPages(guilds=guilds), delete_message_after=False, clear_reactions_after=False, timeout=120, cog=self, page_start=page).start(
            ctx=ctx,
        )

    @log.catch(reraise=True)
    async def bytes_download(self, url: Union[discord.Asset, discord.Attachment, str]) -> tuple[Union[BytesIO, bool], Union[str, bool]]:
        curl: CurlAsyncHTTPClient = global_curl()

        if isinstance(url, (discord.Asset, discord.Attachment)):
            target = url.url
        else:
            _url = yarl.URL(url)
            if _url.host:
                target = str(_url)

        r = await curl.fetch(target)
        if r.error:
            log.error("Unable to download that asset. {}", target)
            return False, False

        try:
            mime = r.headers["Content-Type"]
        except KeyError:
            mime, ex = guess_type(target)

        return r.buffer, mime

    @commands.cooldown(5, 10, commands.BucketType.guild)
    @commands.command(aliases=["gbanner", "sbanner", "serverbanner"])
    async def guildbanner(self, ctx: commands.Context, invite_code: str = None):
        """Display the server's banner.

        Optionally provide a server's invite or vanity url `;sbanner
        /yea` or `;sbanner /KYWecrAm`

        """
        async with ctx.typing():
            if invite_code:
                _url = URL(invite_code)
                if not (_url.scheme and _url.host):
                    invite_code = invite_code.split("/")[-1]
                    _url = URL(f"https://discord.gg/{invite_code}")
                try:
                    invite = await self.bot.fetch_invite(str(_url))
                except discord.NotFound:
                    return await ctx.send(embed=make_e(f"**{invite_code}** is not a valid invite", 3))
                guild: discord.Guild = invite.guild
            else:
                guild: discord.Guild = ctx.guild
            if not guild.banner_url:
                return await ctx.send(embed=make_e(f"{guild} does not have a banner set", 2))
            banner_url = yarl.URL(str(guild.banner_url))
            if "a_" in banner_url.path:
                banner_url = str(banner_url).replace("webp", "gif")
            banner_url = str(banner_url)
            embed = discord.Embed(description=f"{guild}'s banner")

            embed.set_image(url=str(banner_url))
            if embed in color_tasks:
                with suppress(asyncio.TimeoutError):
                    async with asyncio.timeout(1):
                        await color_tasks[embed]
            return await ctx.send(embed=embed)

    @commands.cooldown(2, 12, commands.BucketType.user)
    @commands.command(aliases=["gicon", "sicon", "servericon"])
    async def guildicon(self, ctx: commands.Context, invite_code: str = None):
        """Display the server's icon.

        Optionally provide a server's invite or vanity url `;sicon /yea`
        or `;sicon /KYWecrAm`

        """
        async with ctx.typing():
            if invite_code:
                _url = URL(invite_code)
                if not (_url.scheme and _url.host):
                    invite_code = invite_code.split("/")[-1]
                    _url = URL(f"https://discord.gg/{invite_code}")
                try:
                    invite: discord.Invite = await self.bot.fetch_invite(str(_url))
                    guild = invite.guild
                except discord.NotFound:
                    return await ctx.send(embed=make_e(f"**{invite_code}** is not a valid invite", 3))
            else:
                guild: discord.Guild = ctx.guild
            icon_url = str(guild.icon_url)

            embed = discord.Embed(description=f"{guild}'s icon")

            embed.set_image(url=icon_url)
            if embed in color_tasks:
                with suppress(asyncio.TimeoutError):
                    async with asyncio.timeout(1):
                        await color_tasks[embed]

            return await ctx.send(embed=embed)

    @commands.cooldown(5, 10, commands.BucketType.guild)
    @commands.command(aliases=["ssplash", "gsplash", "splash"])
    async def guildsplash(self, ctx: commands.Context, invite_code: str = None):
        """Display the server's splash image.

        Optionally provide a server's invite or vanity url `;sbanner
        /yea` or `;sbanner /KYWecrAm`

        """
        async with ctx.typing():
            if invite_code:
                _url = URL(invite_code)
                if not (_url.scheme and _url.host):
                    invite_code = invite_code.split("/")[-1]
                    _url = URL(f"https://discord.gg/{invite_code}")
                try:
                    invite = await self.bot.fetch_invite(str(_url))
                except discord.NotFound:
                    return await ctx.send(embed=make_e(f"**{invite_code}** is not a valid invite", 3))

                guild: discord.Guild = invite.guild
            else:
                guild: discord.Guild = ctx.guild
            if not guild.splash_url:
                return await ctx.send(embed=make_e(f"{guild} does not have a banner set", 2))
            splash_url = yarl.URL(str(guild.splash_url))
            if "a_" in splash_url.path:
                splash_url = str(splash_url).replace("webp", "gif")
            embed = discord.Embed(description=f"{guild}'s banner")

            embed.set_image(url=str(splash_url))
            if embed in color_tasks:
                with suppress(asyncio.TimeoutError):
                    async with asyncio.timeout(1):
                        await color_tasks[embed]

            return await ctx.send(embed=embed)
