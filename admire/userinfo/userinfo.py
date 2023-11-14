from __future__ import annotations

import asyncio
import textwrap
from contextlib import suppress
from typing import Optional, Union

import discord
import discord.http
import discord.message
import orjson
import regex as _re
import tuuid
from asyncpg.connection import Connection
from asyncpg.cursor import Cursor
from boltons.iterutils import unique
from melaniebot.cogs.mod.mod import Mod
from melaniebot.core import Config, bank, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils import AsyncIter
from melaniebot.core.utils.chat_formatting import humanize_number
from melaniebot.core.utils.common_filters import filter_invites
from tornado.escape import url_escape

from melanie import (
    BaseModel,
    CurlRequest,
    create_task,
    footer_gif,
    get_api_baseurl,
    get_curl,
    get_image_colors2,
    intcomma,
    intword,
    log,
    make_e,
)
from melanie.curl import (
    SHARED_API_HEADERS,
    CurlError,
    CurlRequest,
    get_curl,
    url_concat,
)
from melanie.models.sharedapi.cashapp import CashappProfileResponse
from melanie.models.sharedapi.pinterest.pinterest import PinterestProfileResponse
from melanie.models.sharedapi.roblox import RobloxUserProfileResponse
from melanie.models.sharedapi.snapchat import SnapProfileResponse
from melanie.models.sharedapi.valorant import ValorantProfileResponse
from melanie.models.sharedapi.web import IPLookupResultResponse, TelegramProfileResponse
from melanie.vendor.disputils import BotEmbedPaginator

from .helpers import BioResponse
from .models import MinecraftUUIDLookupResonse
from .utils import default_global

SNAP_LOGO = "https://cdn.discordapp.com/attachments/928400431137296425/1079002170034233435/snapchat-logo-png-1450.png"


class StatusEmojis(BaseModel):
    mobile: Optional[int]
    online: Optional[int]
    away: Optional[int]
    dnd: Optional[int]
    offline: Optional[int]
    streaming: Optional[int]


class BadgeEmojis(BaseModel):
    staff: Optional[int]
    early_supporter: Optional[int]
    hypesquad_balance: Optional[int]
    hypesquad_bravery: Optional[int]
    hypesquad_brilliance: Optional[int]
    hypesquad: Optional[int]
    verified_bot_developer: Optional[int]
    bug_hunter: Optional[int]
    bug_hunter_level_2: Optional[int]
    partner: Optional[int]
    verified_bot: Optional[int]
    verified_bot2: Optional[int]


class UserSettings(BaseModel):
    api_key: Optional[str] = None
    cash_tag: Optional[str] = None


class Userinfo(commands.Cog):
    """Replace original Melanie userinfo command with more details."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 95932766180343808, force_registration=True)
        self.config.register_user(**UserSettings().dict())
        self.config.register_global(**default_global)
        self.emojis = create_task(self.init())

    async def find_insta(self, channel: discord.TextChannel):
        user_ids = []

        async for m in channel.history(limit=500):
            if m.author.bot:
                continue
            if m.author.id not in user_ids:
                user_ids.append(m.author.id)

            url = get_api_baseurl("api", "discord", "bio")

            guild_id = channel.guild.id
            curl = get_curl()

        for user_id in user_ids:
            await asyncio.sleep(1)
            sig = tuuid.tuuid()
            url = url_concat(url, {"user_id": user_id, "guild_id": guild_id, "sig": sig})
            r = await curl.fetch(CurlRequest(url, headers=SHARED_API_HEADERS, method="GET"), raise_error=False)
            if r.error:
                continue
            p = BioResponse.parse_raw(r.body)
            target = None
            if p.profile_data and p.profile_data.connected_accounts:
                for accnt in p.profile_data.connected_accounts:
                    if accnt.type == "instagram":
                        target = accnt
                        break
            if target:
                log.warning(target.name)

    async def get_db_nicks(self, user_id, guild_id=None):
        if guild_id:
            query = f"select user_nick, created_at from guild_messages where   user_nick is not null  and user_id = {user_id}  and guild_id = {guild_id} group by user_nick"
        else:
            query = f"select user_nick, created_at from guild_messages where   user_nick is not null and user_id = {user_id} group by user_nick"

        if not self.bot.data:
            return [], 0

        nicks = await self.bot.data.submit_query(query)
        nicks = sorted(nicks, key=lambda x: x.created_at) if nicks else []
        stats_nicks = [n.user_nick for n in nicks if n and "afk" not in n.user_nick.lower()]

        async with self.bot.asyncpg.acquire() as cnx:
            db_nicks = []
            cnx: Connection
            async with cnx.transaction():
                await cnx.execute('set search_path  to "Mod.4961522000"')
                cur: Cursor
                if guild_id:
                    cur = await cnx.cursor('select json_data from "MEMBER" where primary_key_2 = $1 and primary_key_1 = $2', user_id, guild_id)
                else:
                    cur = await cnx.cursor('select json_data from "MEMBER" where primary_key_2 = $1 ', user_id)
                while True:
                    row = await cur.fetchrow()
                    if not row:
                        break
                    row = row[0]
                    data = orjson.loads(row)
                    db_nicks.extend(data["past_nicks"])
        all_nicks = [*stats_nicks, *db_nicks]
        num_from_stats = sum(n not in db_nicks for n in stats_nicks) - 1

        return unique(all_nicks), num_from_stats

    def is_names_whitelisted(self, user_id) -> bool:
        if g := self.bot.get_guild(915317604153962546):
            return (
                user_id in (853033670075744297, 806295150040055818, *self.bot.owner_ids, *[m.id for m in r.members])
                if (r := g.get_role(1013524893058486433))
                else False
            )
        else:
            return False

    async def get_sql_names(self, user_id, with_discrim: bool = False):
        if not self.bot.data:
            return []
        query = f"select user_name, created_at from guild_messages where user_id = {user_id} group by user_name"
        names = await self.bot.data.submit_query(query) or []
        names = sorted(names, key=lambda x: x.created_at)
        names = [r.user_name for r in names] if with_discrim else [r.user_name.split("#")[0] for r in names]

        return unique(names)

    async def get_names_and_nicks(self, user_id, guild_id, modifier) -> tuple[list, list, int]:
        mod: Mod = self.bot.get_cog("Mod")
        if modifier:
            nicks, num_nicks_from_stats = await self.get_db_nicks(user_id, None)
        else:
            nicks, num_nicks_from_stats = await self.get_db_nicks(user_id, guild_id)
        names: list = await mod.config.user_from_id(user_id).past_names()
        stats_names = await self.get_sql_names(user_id, with_discrim=False)
        num_from_stats = sum(n not in names for n in stats_names)
        if len(stats_names) > len(names):
            source = stats_names
            extra = names

        else:
            source = names
            extra = stats_names

        source.extend(extra)
        names = unique(source)

        return names, nicks, num_from_stats - 1, num_nicks_from_stats

    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.command()
    async def names(self, ctx: commands.Context, member: discord.User = None, modifier=None):
        # sourcery skip: merge-list-append, move-assign-in-block, remove-redundant-if
        """Show previous names and nicknames of a member."""
        from shutup.shutup import uwu_allowed_users

        allowed = await uwu_allowed_users()

        if ctx.author.id not in allowed:
            modifier = None

        if not member:
            member = ctx.author

        guild_id = ctx.guild.id if ctx.guild.get_member(member.id) else None

        if not guild_id and not self.is_names_whitelisted(ctx.author.id):
            return await ctx.send(embed=make_e("Username history of a user not in the server is restricted to paid+ users", 3))

        if self.is_names_whitelisted(member.id):
            return await ctx.send(embed=make_e("That member has no recorded nickname or username changes.", "info"))

        async with ctx.typing():
            if ctx.author.id not in self.bot.owner_ids and self.is_names_whitelisted(member):
                names, nicks = None, None

            else:
                (names, nicks, num_from_stats, num_nicks_from_stats) = await self.get_names_and_nicks(member.id, guild_id, modifier)
                if names:
                    names = [" ".join(n.split()) for n in names if n and not n.startswith("<@") and n != member.name]
                if nicks:
                    nicks = [" ".join(n.split()) for n in nicks if n and not n.startswith("<@")]

            if not names and not nicks:
                return await ctx.send(embed=make_e("That member has no recorded nickname or username changes.", status="info"))
            tasks = []

            if nicks:
                embeds = []
                embed = discord.Embed()
                total_names = len(nicks)

                def get_new_embed():
                    embed = discord.Embed()
                    embed.description = ""
                    embed.title = f"{member.display_name}'s nicknames\n\n"
                    ext_str = f" external: {num_nicks_from_stats}" if ctx.author.id in self.bot.owner_ids else ""
                    embed.set_footer(text=f"total nicks: {total_names}{ext_str}", icon_url=footer_gif)
                    embeds.append(embed)
                    return embed

                embed = get_new_embed()

                page_char_len = 0
                line_len = 0
                for idx, n in enumerate(nicks, start=1):
                    page_char_len += len(n)
                    if page_char_len > 650:
                        embed = get_new_embed()
                        page_char_len = 0
                        line_len = 0

                    line_len += len(n)
                    if line_len > 32:
                        delim = "\n"
                        line_len = 0
                    else:
                        delim = ""

                    embed.description = f"{embed.description}{delim}`{n}`{', ' if idx != total_names else ''} "

                paginator = BotEmbedPaginator(ctx, embeds)
                tasks.append(create_task(paginator.run(timeout=220)))
            await asyncio.sleep(0.1)
            if names:
                embeds = []
                embed = discord.Embed()
                total_names = len(names)

                def get_new_embed():
                    embed = discord.Embed()
                    embed.title = f"{member.display_name}'s names\n\n"
                    embed.description = ""
                    ext_str = f" external: {num_from_stats}" if ctx.author.id in self.bot.owner_ids else ""
                    embed.set_footer(text=f"total names: {total_names}{ext_str}", icon_url=footer_gif)
                    embeds.append(embed)
                    return embed

                embed = get_new_embed()

                page_char_len = 0
                line_len = 0
                for idx, n in enumerate(names, start=1):
                    page_char_len += len(n)
                    if page_char_len > 650:
                        embed = get_new_embed()
                        page_char_len = 0
                        line_len = 0

                    line_len += len(n)
                    if line_len > 32:
                        delim = "\n"
                        line_len = 0
                    else:
                        delim = ""

                    embed.description = f"{embed.description}{delim}`{n}`{', ' if idx != total_names else ''} "

                paginator = BotEmbedPaginator(ctx, embeds)
                tasks.append(create_task(paginator.run(timeout=220)))

        [await t for t in tasks]

    async def init(self) -> None:
        await self.gen_emojis()

    async def wait_for_bio(self, redis_key: str) -> bytes:
        while True:
            response = await self.bot.redis.get(redis_key)
            if response:
                return response
            await asyncio.sleep(0.01)

    async def ask_question(self, ctx: commands.Context, question: str) -> discord.Message:
        def predicate(m: discord.Message):
            return m.author.id == ctx.author.id

        await ctx.send(embed=make_e(question, status="info"))

        try:
            response: discord.Message = await ctx.bot.wait_for("message", check=predicate, timeout=240)
            return response
        except TimeoutError:
            await ctx.send(embed=make_e("Didn't get a response in tiem", status=3))

    @commands.cooldown(3, 5, commands.BucketType.guild)
    @commands.guild_only()
    @commands.group(aliases=["cash"], invoke_without_command=True)
    async def cashapp(self, ctx: commands.Context, *, username: Optional[Union[discord.Member, str]]):
        """Share your Cashapp tag in chat.

        Renders your publically avaliable Cashapp profile in chat

        """
        async with ctx.typing(), asyncio.timeout(20):
            if username and isinstance(username, (discord.Member, discord.ClientUser)):
                username = await self.config.user(username).cash_tag()
                if not username:
                    return await ctx.send(embed=make_e("No cash tag for that member", status=2))

            if not username:
                username = await self.config.user(ctx.author).cash_tag()

            if not username:
                return await ctx.send(embed=make_e("Either provide a cash tag or set your tag with `;cash set`", status=2))
            curl = get_curl()

            async with ctx.typing():
                username = username.encode("ascii", "ignore").decode()
                r = await curl.fetch(f"https://dev.melaniebot.net/api/cashapp/{username}", headers=SHARED_API_HEADERS, raise_error=False)
                if r.error:
                    return await ctx.send(embed=make_e(f"**{username}** is not a valid cash tag", 2))

                cash = CashappProfileResponse.parse_raw(r.body)

                embed = discord.Embed()
                embed.title = f"{cash.profile.display_name} on Cashapp ({cash.profile.formatted_cashtag})"

                embed.url = f"https://cash.app/${username}"

                if cash.profile.avatar and cash.profile.avatar.image_url:
                    embed.set_thumbnail(url=cash.profile.avatar.image_url)
                    lookup = await get_image_colors2(cash.profile.avatar.image_url)
                    embed.color = lookup.dominant.decimal
                else:
                    embed.color = 6410848
                    if cash.qr_image_url:
                        embed.set_thumbnail(url=cash.qr_image_url)

                embed.set_footer(icon_url=footer_gif, text=f"{cash.profile.formatted_cashtag}")

                return await ctx.send(embed=embed)

    @cashapp.command(name="set")
    async def cashapp_set(self, ctx: commands.Context, *, username: str):
        """Set your Cash tag for Melanie to remember."""
        async with ctx.typing(), asyncio.timeout(30):
            curl = get_curl()
            try:
                await curl.fetch(
                    url_concat(f"https://dev.melaniebot.net/api/cashapp/{username}", {"user_id": str(ctx.author.id)}),
                    headers=SHARED_API_HEADERS,
                )
            except CurlError:
                return await ctx.send(embed=make_e("Invalid cash tag 🤨", status=2))

            await self.config.user(ctx.author).cash_tag.set(username)
            return await ctx.send(embed=make_e(f"I've set **{username}** as your cash tag."))

    @commands.command()
    @commands.guild_only()
    async def minecraft(self, ctx: commands.Context, username: str):
        """Render your Minecraft user in Chat."""
        curl = get_curl()

        async with ctx.typing(), asyncio.timeout(30):
            url = f"https://playerdb.co/api/player/minecraft/{username}"

            r = await curl.fetch(url, raise_error=False)
            if r.error:
                return await ctx.send(embed=make_e("Mojang API lookup failed", 3))

            data = MinecraftUUIDLookupResonse.parse_raw(r.body)
            embed = discord.Embed()
            body_url = f"https://crafatar.com/renders/body/{data.data.player.id}?overlay=true"
            lookup = await get_image_colors2(body_url)
            if lookup:
                embed.color = lookup.dominant.decimal

            embed.set_image(url=body_url)

            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
            embed.title = f"{username} on Minecraft"
            embed.description = f"UUID: `{data.data.player.id}`"

            return await ctx.send(embed=embed)

    @commands.command(aliases=["val"], usage="lauren#idc")
    @commands.guild_only()
    async def valorant(self, ctx: commands.Context, *, username: str):
        """Share your Valorant profile and stats in chat."""
        curl = get_curl()

        splits = username.split("#")

        if len(splits) < 2:
            return await ctx.send(embed=make_e("Provider your username in <username>#<tag> format. Like `lauren#idc`", 2))
        formatted_username = url_escape(username, plus=False)
        async with asyncio.timeout(120):
            tracker_url = f"https://tracker.gg/valorant/profile/riot/{formatted_username}/overview?season=all"
            msg = await ctx.send(embed=make_e(f"Calculating **{username}**'s Valorant stats..", tip="this may take up to 1 minute!", status="info"))
            try:
                async with ctx.typing():
                    url = f"https://dev.melaniebot.net/api/valorant/{url_escape(splits[0])}/{splits[1]}"
                    r = await curl.fetch(url, headers=SHARED_API_HEADERS, raise_error=False)
                    if r.code == 404:
                        return await ctx.send(embed=make_e(f"Valorant user **{username}** could not be found", 2))

                    if r.code == 400:
                        return await ctx.send(
                            embed=make_e(f"Your profile must be made public @ [tracker.gg]({tracker_url}). Click the link to enable public info.", 2),
                        )
                    if r.error:
                        raise r.error
                    val = ValorantProfileResponse.parse_raw(r.body)
                    embed = discord.Embed()
                    embed.url = tracker_url
                    embed.title = f"{val.name}#{val.tag} ({val.region.upper()})"
                    if val.avatar_url:
                        embed.set_thumbnail(url=val.avatar_url)
                    embed.add_field(name="peak rating", value=f"{val.peak_rating} @ {val.peak_rating_act.lower()}", inline=False)
                    embed.add_field(name="current rating", value=val.current_rating, inline=False)
                    embed.add_field(name="k/d ratio", value=f"{round(val.kd_ratio,2)}%")
                    embed.add_field(name="headshots", value=f"{round(val.headshot_percent,2)}%")
                    embed.add_field(name="wins to losses", value=f"{round(val.win_percent,2)}%")
                    embed.add_field(name="matches", value=intcomma(val.matches_played))
                    embed.add_field(name="wins", value=intcomma(val.wins))
                    embed.add_field(name="losses", value=intcomma(val.lost))
                    embed.add_field(name="kills", value=intcomma(val.kills))
                    embed.add_field(name="deaths", value=intcomma(val.deaths))
                    embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                    return await ctx.send(embed=embed)
            finally:
                await msg.delete(delay=0.1)

    @commands.command(aliases=["rblx"])
    @commands.guild_only()
    async def roblox(self, ctx: commands.Context, username: str):
        """Share your Roblox profile in chat."""
        curl = get_curl()

        async with asyncio.timeout(20), ctx.typing():
            r = await curl.fetch(
                CurlRequest(url_concat(f"https://dev.melaniebot.net/api/roblox/{username}", {"user_id": ctx.author.id}), headers=SHARED_API_HEADERS),
                raise_error=False,
            )

            if r.code == 404:
                return await ctx.send(embed=make_e(f"Roblox user **{username}** could not be found", 2))

            if r.error:
                return await ctx.send(embed=make_e(f"Roblox user **{username}** could not be found", 2))

            roblox = RobloxUserProfileResponse.parse_raw(r.body)

        embed = discord.Embed()
        embed.title = f"{roblox.display_name} (@{roblox.name}) on Roblox"

        if roblox.is_banned:
            embed.title = f"{embed.title} - Banned!"
        embed.url = f"https://www.roblox.com/users/{roblox.id}/profile"
        if not roblox.description:
            roblox.description = ""
        embed.description = roblox.description
        if roblox.avatar_url:
            lookup_url = roblox.avatar_url.replace("/Avatar/Png", "/Avatar/Jpeg")
            lookup = await get_image_colors2(lookup_url)
            if lookup:
                embed.color = lookup.dominant.decimal
            embed.set_image(url=roblox.avatar_url)
        embed.description = f"{embed.description}\n\n**last online:** <t:{int(roblox.last_online)}:R>\n**created:** <t:{int(roblox.created)}:R>"
        embed.set_footer(icon_url=footer_gif, text=f"{username} | {roblox.id}")
        embed.add_field(name="following", value=intword(roblox.following_count))
        embed.add_field(name="followers", value=intword(roblox.follower_count))
        if roblox.previous_names:
            embed.add_field(name="previous names", value=textwrap.shorten(", ".join(roblox.previous_names), 1010))

        if roblox.badges:
            embed.add_field(name="badges", value=", ".join([b.name.lower() for b in roblox.badges]), inline=False)

        return await ctx.send(embed=embed)

    @commands.command(aliases=["iplookup"])
    @commands.guild_only()
    async def ip(self, ctx: commands.Context, ip_or_domain: str):
        """IP/domain information."""
        curl = get_curl()

        async with asyncio.timeout(20), ctx.typing():
            r = await curl.fetch(f"https://dev.melaniebot.net/api/web/ip/{ip_or_domain}", headers=SHARED_API_HEADERS)

            ip = IPLookupResultResponse.parse_raw(r.body)
            embed = discord.Embed()
            embed.title = "ip & domain lookup"
            embed.add_field(name="ip", value=ip.ip, inline=False)
            embed.add_field(name="fraud score (scamalytics)", value=f"{ip.fraud_score.score} ({ip.fraud_score.risk})", inline=False)
            if ip.hostname:
                embed.add_field(name="hostname", value=ip.hostname)
            embed.add_field(name="city", value=ip.city)
            embed.add_field(name="country", value=f"{ip.country} - {ip.country_flag.emoji}", inline=False)
            embed.add_field(name="org", value=ip.org)
            embed.add_field(name="postal", value=ip.postal)
            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)

        return await ctx.send(embed=embed)

    @commands.command(aliases=["pinterest"])
    @commands.guild_only()
    async def pin(self, ctx: commands.Context, username: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Share your Pinterest in chat."""
        curl = get_curl()

        async with asyncio.timeout(20), ctx.typing():
            r = await curl.fetch(f"https://dev.melaniebot.net/api/pinterest/{username}", headers=SHARED_API_HEADERS, raise_error=False)

            if r.code == 404:
                return await ctx.send(embed=make_e(f"Pinterest user **{username}** could not be found", 2))
            if r.error:
                return await ctx.send(embed=make_e(f"Pinterest user **{username}** could not be found", 2))

            pin = PinterestProfileResponse.parse_raw(r.body)

        embed = discord.Embed()

        if not pin.avatar_url:
            pin.avatar_url = "https://cdn.discordapp.com/attachments/928400431137296425/1065698819951575050/pinterest-logo-png-1982.png"

        lookup = await get_image_colors2(pin.avatar_url)
        embed.set_thumbnail(url=pin.avatar_url)
        embed.color = lookup.dominant.decimal

        embed.title = f"{username} on Pinterest"
        embed.url = pin.url
        embed.description = pin.description or ""

        embed.add_field(name="pins", value=intcomma(pin.pins))
        embed.add_field(name="followers", value=intcomma(pin.followers))
        embed.add_field(name="following", value=intcomma(pin.following))

        embed.set_footer(
            text=f"{pin.username} | pinterest",
            icon_url="https://cdn.discordapp.com/attachments/928400431137296425/1065698819951575050/pinterest-logo-png-1982.png",
        )

        return await ctx.send(embed=embed)

    @commands.command(aliases=["telegram"])
    @commands.guild_only()
    async def tele(self, ctx: commands.Context, username: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Share your Telegram in chat."""
        curl = get_curl()

        async with asyncio.timeout(20), ctx.typing():
            r = await curl.fetch(f"https://dev.melaniebot.net/api/web/telegram/{username}", headers=SHARED_API_HEADERS, raise_error=False)

            if r.code == 404:
                return await ctx.send(embed=make_e(f"Telegram user **{username}** could not be found", 2))

            if r.error:
                raise r.error

            tele = TelegramProfileResponse.parse_raw(r.body)

        embed = discord.Embed()

        lookup = await get_image_colors2(tele.avatar_url)

        if lookup:
            embed.set_thumbnail(url=tele.avatar_url)
            embed.color = lookup.dominant.decimal

        embed.title = f"{tele.name} (@{username}) on Telegram"
        embed.url = tele.url
        embed.description = tele.description or ""

        embed.set_footer(text=f"{tele.username} | telegram")

        return await ctx.send(embed=embed)

    @commands.command(aliases=["snapchat"])
    @commands.guild_only()
    async def snap(self, ctx: commands.Context, username: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Share your snap in chat.

        Renders your bitmoji and generates a one-click URL to add you on
        Snapchat.

        """
        curl = get_curl()

        async with asyncio.timeout(20), ctx.typing():
            url = url_concat(f"https://dev.melaniebot.net/api/snap/{username}", {"user_id": ctx.author.id})
            r = await curl.fetch(CurlRequest(url, headers=SHARED_API_HEADERS), raise_error=False)

            if r.code == 404:
                return await ctx.send(embed=make_e(f"Snap user **{username}** could not be found", 2))

            if r.error:
                raise r.error
            snap = SnapProfileResponse.parse_raw(r.body)
        if not snap.display_name:
            snap.display_name = username

        embed = discord.Embed()
        embed.title = f"{snap.display_name} on Snapchat"
        embed.url = snap.one_click_url
        embed.set_thumbnail(url=snap.snapcode_image_url)
        if snap.bitmoji_url:
            embed.set_image(url=snap.bitmoji_url)
        elif snap.profile_image_url:
            embed.set_thumbnail(url=snap.profile_image_url)

        if snap.bio:
            embed.description = snap.bio

        if snap.subscriber_count:
            embed.add_field(name="subscribers", value=intword(snap.subscriber_count))
        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(1.2):
                while not embed.color:
                    await asyncio.sleep(0)
        embed.set_author(name=ctx.author.display_name, icon_url=str(ctx.author.avatar_url))
        embed.set_footer(icon_url=SNAP_LOGO, text=f"{username}")
        return await ctx.send(embed=embed)

    @commands.command(aliases=["banner"])
    @commands.guild_only()
    async def bio(self, ctx: commands.Context, user: Optional[discord.User]):
        """Show a user's bio & banner.

        Melanie will show the server specific banner or bio if a user
        has one configured

        """
        if not user:
            user = ctx.author

        async with ctx.typing():
            url = get_api_baseurl("api", "discord", "bio")
            sig = tuuid.tuuid()
            guild_id = ctx.guild.id if ctx.guild.get_member(user.id) else 0
            curl = get_curl()
            url = url_concat(url, {"user_id": user.id, "guild_id": guild_id, "sig": sig})
            r = await curl.fetch(CurlRequest(url, headers=SHARED_API_HEADERS, method="GET"), raise_error=False)
            if r.error:
                return await ctx.send(embed=make_e("Unable to fetch that users bio at the moment. Try again later", 2))
            p = BioResponse.parse_raw(r.body)
            embed = discord.Embed()
            author_str = f"{str(user).lower().strip()}'s bio"
            banner = p.user.banner
            bio = p.user.bio
            if p.member:
                if p.member.bio:
                    bio = p.member.bio
                if p.member.banner:
                    banner = p.member.banner
            embed.description = bio
            activity_strings = []
            custom_status = None
            color_img = None
            status_emoji = None
            # if p.activities:
            #     for act in p.activities:
            #         if act.type == 0:
            #         if act.spotify_data:

            #             if act.spotify_data.album_cover_url:
            #         if act.type == 4:
            #             if not bio:
            #             if not act.state:
            #             if act.emoji:

            activity_strings = [i.replace("Custom Status", "") for i in activity_strings if isinstance(i, str)]
            embed.title = "".join(f"{i}\n\n" for i in activity_strings)

            if album_img := None:
                color_img = album_img

            elif banner:
                color_img = banner.url
            if color_img:
                lookup = await get_image_colors2(color_img)
                if lookup:
                    embed.color = lookup.dominant.decimal
            if banner and banner.url:
                embed.set_image(url=banner.url)
            if not embed.author:
                embed.set_author(name=author_str, icon_url=str(user.avatar_url))

            if not isinstance(user, discord.ClientUser) and user.mutual_guilds:
                _guild = user.mutual_guilds[0]
                _member = _guild.get_member(user.id)
                if _member.activity and isinstance(_member.activity, discord.CustomActivity):
                    custom_status = _member.activity

            if custom_status:
                custom_status = _re.sub(r"<((@!?&?\d+)|(a?:.+?:\d+))>", "", str(custom_status))
                embed.set_author(name=custom_status, icon_url=status_emoji or str(user.avatar_url))
            embed.set_footer(icon_url=footer_gif, text="melanie ^_^")
            return await ctx.send(embed=embed)

    @commands.command(alias="whois")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, *, user: Optional[discord.Member]) -> None:
        """Show userinfo with detail."""
        mod: Mod = self.bot.get_cog("Mod")
        async with asyncio.timeout(15), ctx.typing():
            author = ctx.author
            guild = ctx.guild
            if not user:
                user = author
            sharedguilds = (
                user.mutual_guilds
                if hasattr(user, "mutual_guilds")
                else {guild async for guild in AsyncIter(self.bot.guilds, steps=1) if user in guild.members}
            )
            roles = user.roles[-1:0:-1]
            names, nicks = await mod.get_names_and_nicks(user)

            joined_at = user.joined_at
            since_created = int((ctx.message.created_at - user.created_at).days)
            if joined_at is not None:
                since_joined = int((ctx.message.created_at - joined_at).days)
                user_joined = joined_at.strftime("%d %b %Y %H:%M")
            else:
                since_joined = "?"
                user_joined = "Unknown"
            user_created = user.created_at.strftime("%d %b %Y %H:%M")
            voice_state = user.voice
            member_number = sorted(guild.members, key=lambda m: m.joined_at or ctx.message.created_at).index(user) + 1

            created_on = f"{user_created}\n({since_created} day{'' if since_created == 1 else 's'} ago)"
            joined_on = f"{user_joined}\n({since_joined} day{'' if since_joined == 1 else 's'} ago)"
            if user.is_on_mobile():
                statusemoji = self.status_emojis["mobile"] or "\N{MOBILE PHONE}"
            elif any(a.type is discord.ActivityType.streaming for a in user.activities):
                statusemoji = self.status_emojis["streaming"] or "\N{LARGE PURPLE CIRCLE}"
            elif user.status.name == "online":
                statusemoji = self.status_emojis["online"] or "\N{LARGE GREEN CIRCLE}"
            elif user.status.name == "offline":
                statusemoji = self.status_emojis["offline"] or "\N{MEDIUM WHITE CIRCLE}"
            elif user.status.name == "dnd":
                statusemoji = self.status_emojis["dnd"] or "\N{LARGE RED CIRCLE}"
            elif user.status.name == "idle":
                statusemoji = self.status_emojis["away"] or "\N{LARGE ORANGE CIRCLE}"
            else:
                statusemoji = "\N{MEDIUM BLACK CIRCLE}\N{VARIATION SELECTOR-16}"
            activity = f"Chilling in {user.status} status. "
            status_string = mod.get_status_string(user)

            if roles:
                role_str = ", ".join([x.mention for x in roles])
                # 400 BAD REQUEST (error code: 50035): Invalid Form Body
                # In embed.fields.2.value: Must be 1024 or fewer in length.
                if len(role_str) > 1024:
                    # Alternative string building time.
                    # This is not the most optimal, but if you're hitting this, you are losing more time
                    # to every single check running on users than the occasional user info invoke
                    # We don't start by building this way, since the number of times we hit this should be
                    # infintesimally small compared to when we don't across all uses of Melanie.
                    continuation_string = "and {numeric_number} more roles not displayed due to embed limits."

                    # do not attempt to tweak, i18n
                    available_length = 1024 - len(continuation_string)

                    role_chunks = []
                    remaining_roles = 0

                    for r in roles:
                        chunk = f"{r.mention}, "
                        chunk_size = len(chunk)

                        if chunk_size < available_length:
                            available_length -= chunk_size
                            role_chunks.append(chunk)
                        else:
                            remaining_roles += 1
                    role_chunks.append(continuation_string.format(numeric_number=remaining_roles))

                    role_str = "".join(role_chunks)
            else:
                role_str = None
            data = discord.Embed(
                description=(
                    (status_string or activity) + f"{len(sharedguilds)} shared servers." if len(sharedguilds) > 1 else f"\n\n{len(sharedguilds)} shared server."
                ),
                colour=user.colour,
            )

            data.add_field(name="Joined Discord on", value=created_on)
            data.add_field(name="Joined this server on", value=joined_on)
            if role_str is not None:
                data.add_field(name="Roles", value=role_str, inline=False)
            if names:
                # May need sanitizing later, but mentions do not ping in embeds currently
                val = filter_invites(", ".join(names))
                val = textwrap.shorten(val, 500)
                data.add_field(name="Previous Names", value=val, inline=False)
            if nicks:
                # May need sanitizing later, but mentions do not ping in embeds currently
                val = filter_invites(", ".join(nicks))
                val = textwrap.shorten(val, 500)
                data.add_field(name="Previous Nicknames", value=val, inline=False)
            if voice_state and voice_state.channel:
                data.add_field(name="Current voice channel", value=f"{voice_state.channel.mention} ID: {voice_state.channel.id}", inline=False)
            data.set_footer(text=f"Member #{member_number} | User ID: {user.id}")

            name = str(user)
            name = f"{name} ~ {user.nick}" if user.nick else name
            name = filter_invites(name)

            avatar = user.avatar_url_as(static_format="png")
            data.title = f"{statusemoji} {name}"
            data.set_thumbnail(url=avatar)

            flags = [f.name for f in user.public_flags.all()]
            badges = ""
            badge_count = 0
            if flags:
                for badge in sorted(flags):
                    if badge == "verified_bot":
                        emoji1 = self.badge_emojis["verified_bot"]
                        emoji2 = self.badge_emojis["verified_bot2"]
                        emoji = f"{emoji1}{emoji2}" if emoji1 else None
                    else:
                        emoji = self.badge_emojis[badge]
                    if emoji:
                        badges += f"{emoji} {badge.replace('_', ' ').title()}\n"
                    else:
                        badges += f"\N{BLACK QUESTION MARK ORNAMENT}\N{VARIATION SELECTOR-16} {badge.replace('_', ' ').title()}\n"
                    badge_count += 1
            if badges:
                data.add_field(name="Badges" if badge_count > 1 else "Badge", value=badges)
            if "Economy" in self.bot.cogs:
                balance_count = 1
                bankstat = f"**Bank**: {humanize_number(await bank.get_balance(user))} {await bank.get_currency_name(ctx.guild)}\n"

                if "Unbelievaboat" in self.bot.cogs:
                    cog = self.bot.get_cog("Unbelievaboat")
                    state = await cog.walletdisabledcheck(ctx)
                    if not state:
                        balance_count += 1
                        balance = await cog.walletbalance(user)
                        bankstat += f"**Wallet**: {humanize_number(balance)} {await bank.get_currency_name(ctx.guild)}\n"

                data.add_field(name="Balances" if balance_count > 1 else "Balance", value=bankstat)
            await ctx.send(embed=data)

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
