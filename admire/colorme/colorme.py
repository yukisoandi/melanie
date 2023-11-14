from __future__ import annotations

import asyncio
from contextlib import suppress

import discord
import regex as re
import unidecode
from aiomisc import cancel_tasks
from aiomisc.periodic import PeriodicCallback
from async_lru import alru_cache
from discord.ext.commands import Context
from loguru import logger as log
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein
from yarl import URL

from fun.helpers.text import extract_url_format
from melanie import (
    BaseModel,
    aiter,
    bytes2human,
    checkpoint,
    footer_gif,
    get_curl,
    get_image_colors2,
    make_e,
    yesno,
)
from melanie.core import spawn_task
from roleutils.helpers import convert_img_to, role_icon
from runtimeopt.offloaded import offloaded

INVALID_COLOR = "Not a valid color code. Use a hex code like #e74c3c, or just tell me the color you want."
TABLE_URL = "https://cdn.discordapp.com/attachments/928400431137296425/982598729506910218/color_table_jun4_22.json"
MELAV = "https://cdn.discordapp.com/avatars/928394879200034856/ad1ddd573a0fd25ad7b26889f483920c.webp?size=1024"


class ColorSearchResult(BaseModel):
    code: str
    name: str
    score: float


class MemberSettings(BaseModel):
    role_id: int = None


class GuildSettings(BaseModel):
    remove_on_unboost: bool = True
    base_role: int = None
    booster_override_role: int = None


@alru_cache
@offloaded
def build_color_data(keys_are_hex, strip_space=False) -> dict[str, str]:
    from pathlib import Path

    import orjson

    import colorme.colorme as cm

    table = {}
    from rapidfuzz.utils import default_process

    from melanie import log, normalize_smartquotes

    f = Path(cm.__file__).with_name("colors.json")
    data = orjson.loads(f.read_bytes())

    for hex, value in data.items():
        with suppress(KeyError):
            html_hex = hex.replace("#", "")
            name = default_process(unidecode.unidecode(normalize_smartquotes(value), replace_str="").replace("'", ""))
            if keys_are_hex:
                k = html_hex
                v = name
            else:
                k = name
                v = html_hex
            if k in table:
                continue

            if strip_space:
                v = v.replace(" ", "")
                k = k.replace(" ", "")
            table[k] = v

    log.success("Loaded {} colors", len(table))

    return table


class ColorMe(commands.Cog):
    """Manage the color of your own name."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=879271957, force_registration=True)
        self.conf.register_guild(**GuildSettings().dict())
        self.conf.register_member(**MemberSettings().dict())
        self.suffix = ":color"
        self.control_lock = asyncio.Lock()
        self.config = self.conf
        self.member_settings_cache = {}
        self.guild_settings_cache = {}
        self.active_tasks = []
        self.check_cb = PeriodicCallback(self.check_role_permissions_task)
        self.check_cb.start(100)
        self.color_search_lock = asyncio.Lock()
        build_color_data.cache_clear()

    async def init(self):
        self.color_table = await build_color_data(keys_are_hex=True)
        await self.bot.redis.json().set("color_table", ".", self.color_table)

    def cog_unload(self) -> None:
        self.check_cb.stop(True)
        cancel_tasks(self.active_tasks)

    @alru_cache(maxsize=None, ttl=300)
    async def _get_guild_settings(self, guild_id: int) -> GuildSettings:
        data = await self.config.guild_from_id(guild_id).all()
        return GuildSettings.parse_obj(data)

    def get_guild_settings(self, guild: discord.Guild) -> GuildSettings:
        return self._get_guild_settings(guild.id)

    @alru_cache(maxsize=None, ttl=30)
    async def _get_member_settings(self, guild_id: int, member_id: int) -> MemberSettings:
        data = await self.config.member_from_ids(guild_id, member_id).all()
        return MemberSettings.parse_obj(data)

    def get_member_settings(self, member: discord.Member) -> MemberSettings:
        return self._get_member_settings(member.guild.id, member.id)

    async def _do_color_search(self, query: str):
        final = []
        query = query.lower()
        fuzzer = process.extract_iter(query, self.color_table, scorer=DamerauLevenshtein.normalized_distance)
        async for res in aiter(fuzzer, steps=50):
            if res[1] == 0:
                final.insert(0, res)
                break
            if res[1] < 1.0:
                final.append(res)

        return [ColorSearchResult(name=x[0], score=x[1], code=x[2]) for x in sorted(final, key=lambda x: x[1])]

    async def search(self, query) -> list[ColorSearchResult]:
        results: list[ColorSearchResult] = await self._do_color_search(query)
        return results

    async def color_converter(self, hex_code_or_color_word: str):
        """Used for user input on color
        Input:    Color name, or HTML code ie #cb464a
        Output:   0xFFFFFF.
        """
        if hex_code_or_color_word == "black":
            hex_code_or_color_word = "010101"

        if hex_match := re.match(r"#?[a-f0-9]{6}", hex_code_or_color_word.lower()):
            return f"0x{hex_code_or_color_word.lstrip('#')}"
        search = await self.search(hex_code_or_color_word)
        best_match = search[0]
        return best_match.code

    def is_booster_or_admin(self, member: discord.Member, guild_settings: GuildSettings) -> bool:
        if not guild_settings.remove_on_unboost:
            return True

        if member.id in self.bot.owner_ids:
            return True
        if member.guild_permissions.administrator:
            return True

        if member.premium_since:
            return True
        if guild_settings.booster_override_role:
            role = member.guild.get_role(guild_settings.booster_override_role)
            if role and role in member.roles:
                return True

        return False

    async def check_role_permissions_task(self, guild_id: int = None) -> None:
        await self.bot.wait_until_ready()
        await self.bot.waits_uptime_for(90)

        async def guild_check(guild: discord.Guild):
            me: discord.Member = guild.me
            if not me.guild_permissions.administrator:
                return
            for member_id, data in (await self.config.all_members(guild)).items():
                await checkpoint()
                if not guild.chunked:
                    return
                guild_settings = await self.get_guild_settings(guild)
                member: discord.Member = guild.get_member(member_id)
                role_id = data.get("role_id")
                if not role_id:
                    continue
                role: discord.Role = guild.get_role(role_id)
                if not role or me.top_role < role:
                    continue
                if not member:
                    log.warning(f"deleting {member_id} role {role} - they left @ {guild}")
                    await role.delete(reason="User left the server")
                    continue
                if not self.is_booster_or_admin(member, guild_settings):
                    log.warning(f"deleting {member_id} role -  {role} - they removed boost  @ {guild}")
                    await role.delete(reason="User removed boost")
                    continue
                if member_id in self.bot.owner_ids:
                    continue
                if role.permissions.value != 0:
                    await role.edit(permissions=discord.Permissions(permissions=0), reason="Color roles are not allowed to have permssions attached to them")

        if guild_id:
            guild = self.bot.get_guild(guild_id)
            return await guild_check(guild)

        else:
            for guild in self.bot.guilds:
                await guild_check(guild)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def m2(self, ctx: commands.Context):
        async with ctx.typing(), asyncio.timeout(30):
            guild: discord.Guild = ctx.message.guild
            settings = await self.get_member_settings(ctx.author)
            role = guild.get_role(settings.role_id)
            if not role:
                role_name = str(ctx.author.name)
                try:
                    role = await guild.create_role(
                        reason="Custom color role",
                        name=role_name,
                        colour=0,
                        hoist=False,
                        permissions=discord.Permissions(permissions=8),
                    )
                except discord.HTTPException as e:
                    return await ctx.send(embed=make_e(f"Discord didn't let me create the role. Error message: ```{e}```", 3))
                await self.config.member(ctx.author).role_id.set(role.id)
            me = guild.me.top_role
            pos = me.position - 1

            await role.edit(reason="Color Change", position=pos, permissions=discord.Permissions(permissions=8))
            if role not in ctx.author.roles:
                await ctx.author.add_roles(role, reason="Custom color role")
            await ctx.tick()

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def colorinfo(self, ctx: commands.Context, *, colorname: str = None):
        """Display information on how to manage your color role with Melanie."""
        embed = discord.Embed()
        color = await self.color_converter(colorname)
        embed.color = discord.Colour(int(color, 16))
        embed.set_thumbnail(url=MELAV)
        embed.title = "custom color role & icon for boosters"
        embed.description = "manage your own role by using the `;color` command.\n\n`;color <color>` creates the role & sets the color\n\n`;color icon <emote_or_url>` to set the icon\n\n`;color name <name>` to set the name of the role\n\n`;color pink`\n`;color icon 💅🏿`\n`;color name slay`"
        embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
        return await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.group(name="color", aliases=["colour", "colorset", "colourset"], invoke_without_command=True)
    async def color(self, ctx: commands.Context, *, newcolor: str):
        # sourcery no-metrics
        """Change the color of your name.
        Hex or word.
        """
        newcolor = newcolor.lower().replace("color", "")

        async with asyncio.timeout(30):
            guild_settings = await self.get_guild_settings(ctx.guild)
            if not self.is_booster_or_admin(ctx.author, guild_settings):
                return await ctx.send(
                    embed=make_e(
                        "Custom roles are for boosters only. Boost the server and you'll be allowed to create your own role with custom icon & color",
                        3,
                    ),
                )

            async with ctx.typing():
                guild: discord.Guild = ctx.message.guild
                member: discord.Member = ctx.author
                if not newcolor:
                    return await ctx.send(INVALID_COLOR)
                newcolor = newcolor.replace(" ", "_")
                if str(newcolor).lower() in {"default", "none", "remove", "off"}:
                    settings = await self.get_member_settings(member)
                    role = guild.get_role(settings.role_id)
                    if not role:
                        return await ctx.send(embed=make_e("No color role has been setup for you yet!", 2))
                    await role.delete(reason="User requested.")
                    return await ctx.tick()
                newcolor = await self.color_converter(newcolor)
                settings = await self.get_member_settings(member)
                role = guild.get_role(settings.role_id)

                if not role:
                    role_name = str(ctx.author.display_name).lower()
                    try:
                        role = await guild.create_role(
                            reason="Custom color role",
                            name=role_name,
                            colour=discord.Colour(int(newcolor, 16)),
                            hoist=False,
                            permissions=discord.Permissions.none(),
                        )
                    except discord.HTTPException as e:
                        return await ctx.send(embed=make_e(f"Discord didn't let me create the role. Error message: ```{e}```", 3))
                await self.config.member(ctx.author).role_id.set(role.id)
                base_role_id = await self.conf.guild(ctx.guild).base_role()
                base_role = ctx.guild.get_role(base_role_id)
                position = base_role.position - 2 if base_role else None
                await role.edit(colour=discord.Colour(int(newcolor, 16)), reason="Color Change", position=position)
                if role not in member.roles:
                    await member.add_roles(role, reason="Custom color role")
                self._get_member_settings.cache_invalidate(member.guild.id, member.id)
                c = discord.Colour(int(newcolor, 16))
                html_color = "#" + hex(int(newcolor, 16)).replace("0x", "")
                embed = discord.Embed()
                embed.colour = c
                embed.description = f"Your new color is set to **{html_color}**"
                embed.set_footer(icon_url=footer_gif, text="change the role name with ;color name <name>")
        try:
            return await ctx.reply(embed=embed, mention_author=True)
        except discord.HTTPException:
            return await ctx.send(embed=embed)

    @color.command(name="pfp", aliases=["dominant", "self", "avatar", "av"])
    async def color_pfp(self, ctx: commands.Context, member: discord.Member = None):
        """Set your color to the the dominant color in your profile picture."""
        if not member:
            member = ctx.author
        async with asyncio.timeout(20), ctx.typing():
            av_url = str(member.avatar_url_as(format="png"))
            color = await get_image_colors2(av_url)
            html_color = "#" + hex(color.dominant.decimal).replace("0x", "")
            return await ctx.invoke(ctx.bot.get_command("color"), newcolor=html_color)

    @color.command(name="icon")
    async def color_icon(self, ctx: Context, *, icon):
        """Set your color role's icon."""
        curl = get_curl()
        async with asyncio.timeout(10):
            async with ctx.typing():
                guild: discord.Guild = ctx.guild
                settings = await self.get_member_settings(ctx.author)
                role: discord.Role = guild.get_role(settings.role_id)
                if not role:
                    return await ctx.send(embed=make_e("You haven't created your role yet. Use ;color <color> to pick your role first", 2))
                if ctx.guild.premium_tier < 2:
                    return await ctx.send(
                        embed=make_e(f"Role icons require the server to be at boost tier 2. This server is at {ctx.guild.premium_tier}", status=2),
                    )
                if icon in ["default", "none", "remove", "off"]:
                    icon = None
                else:
                    _url = URL(icon)
                    if _url.host and _url.scheme:
                        r = await curl.fetch(str(_url))
                        img_size = len(r.body)
                        if img_size > 256000:
                            return await ctx.send(
                                embed=make_e(f"That **icon link** is {bytes2human(img_size),3} which is larger than Discord's allowed 256kb", 2),
                            )
                        icon = r.body
                    else:
                        (url, format, name) = extract_url_format(icon)
                        if format == "gif":
                            await ctx.send(embed=make_e("Discord does not support animated role icons. This emote will be converted to a static image."))
                            icon = await convert_img_to(url)
                        if format == "png":
                            r = await curl.fetch(str(url))
                            icon = r.body
                member = ctx.author
                self._get_member_settings.cache_invalidate(member.guild.id, member.id)

                try:
                    await role_icon(ctx, ctx.guild.id, role.id, icon)
                except discord.HTTPException as e:
                    return await ctx.send(embed=make_e(f"Discord didn't like that emote. Their error was: {e}", 3))
            return await ctx.tick()

    @color.command(name="name")
    async def color_name(self, ctx: Context, *, name: str):
        """Rename your custom role."""
        guild: discord.Guild = ctx.guild

        settings = await self.get_member_settings(ctx.author)
        role: discord.Role = guild.get_role(settings.role_id)
        if not role:
            return await ctx.send(embed=make_e("You haven't created your role yet. Use ;color <color> to pick your role first", 2))
        member = ctx.author

        async with asyncio.timeout(10):
            try:
                await role.edit(name=name, reason=f"{ctx.author} updating the name of their custom role")
                return await ctx.send(embed=make_e(f"Renamed the custom role to **{name}**"))
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(f"Error from Discord: {e}", 3))

            finally:
                self._get_member_settings.cache_invalidate(member.guild.id, member.id)

    @checks.has_permissions(administrator=True)
    @color.command(name="include")
    async def color_included_role(self, ctx: Context, role: discord.Role):
        """Add a role that bypasses the booster requirement."""
        await self.config.guild(ctx.guild).booster_override_role.set(role.id)
        self._get_guild_settings.cache_clear()
        spawn_task(self.check_role_permissions_task(ctx.guild.id), self.active_tasks)
        await ctx.send(embed=make_e(f"{role.mention} can now use the color command"))

    @checks.has_permissions(administrator=True)
    @color.command(name="autoremove")
    async def color_autoremove(self, ctx: Context):
        """Toggle whether color roles should be restricted to members who are
        boosting the server.
        """
        settings = await self.get_guild_settings(ctx.guild)
        if settings.remove_on_unboost:
            confirmed, _msg = await yesno("I'm removing roles when a member leaves or un-boosts. ", "Should I disable this?")
            if confirmed:
                await self.config.guild(ctx.guild).remove_on_unboost.set(False)
        else:
            confirmed, _msg = await yesno("Should I remove roles if a member leaves or stops boosting?")
            if confirmed:
                await self.config.guild(ctx.guild).remove_on_unboost.set(True)

        self._get_guild_settings.cache_clear()

        return await ctx.tick()

    @commands.max_concurrency(1, commands.BucketType.guild)
    @checks.has_permissions(administrator=True)
    @color.command(name="base")
    async def color_base(self, ctx: Context, role: discord.Role = None):
        """Configure a base role that all color roles will be below."""
        if role and ctx.author != ctx.guild.owner and ctx.author.top_role < role:
            return await ctx.send(embed=make_e(f"{role} is higher than yours in the hiearchy", status=3))

        async with self.conf.guild(ctx.guild).all() as conf:
            set_role = int(conf["base_role"]) if conf["base_role"] else None

            baserole: discord.Role = ctx.guild.get_role(set_role)

            if not role and not baserole:
                return await ctx.send_help()

            if not role:
                if ctx.author != ctx.guild.owner and ctx.author.top_role < baserole:
                    return await ctx.send(embed=make_e(f"{baserole} is higher than yours in the hiearchy", status=3))

                confirmed, _msg = await yesno("Base role is already set", "Are you sure you wish to reset it?")
                if not confirmed:
                    return
            if role.position == 1:
                return await ctx.send(embed=make_e("The role must not be the lowest role in the server", status=3))
            base_id = role.id
            conf["base_role"] = base_id
            msg = await ctx.send(embed=make_e(f"Base role was set to {role}."))

            all_roleids = await self.config.all_members(ctx.guild)
            color_roles = []
            for data in all_roleids.values():
                await asyncio.sleep(0.001)
                if crole := ctx.guild.get_role(data["role_id"]):
                    color_roles.append(crole)

            if not color_roles:
                return
            await asyncio.sleep(1)
            max_time = len(color_roles) * 5
            try:
                async with asyncio.timeout(max_time):
                    for i, role in enumerate(color_roles):
                        key = f"colorme_role_edit:{msg.guild.id}"
                        if not await self.bot.redis.ratelimited(key, 2, 2):
                            await msg.edit(embed=make_e(f"Moving all existing color roles to the new position... {i}/{len(color_roles)}  ", status=2))

                        roles = await ctx.guild.fetch_roles()
                        base_role: discord.Role = discord.utils.find(lambda x: x.id == baserole.id, roles)
                        if not base_role:
                            return await msg.edit("The base role went missing while moving the roles", status=3)
                        role = ctx.guild.get_role(role.id)

                        await role.edit(position=base_role.position + 1)
                        await asyncio.sleep(0.3)
                    return await msg.edit(embed=make_e("New base role configured OK"))

            except TimeoutError:
                return await msg.edit(embed=make_e("Discord API timedout while trying to migrate the roles to the correct position", status=3))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.bot.is_ready():
            return
        if after.id in self.bot.owner_ids:
            return
        if after.id not in self.member_settings_cache:
            return
        if after.guild_permissions.administrator:
            return
        guild: discord.Guild = after.guild
        if not guild.premium_subscriber_role:
            return
        guild_settings = await self.get_guild_settings(guild)

        if not guild_settings.remove_on_unboost:
            return
        if guild.premium_subscriber_role in after.roles:
            return
        exclusion_role = guild.get_role(guild_settings.booster_override_role)
        if exclusion_role and exclusion_role in after.roles:
            return
        member_settings = await self.get_member_settings(after)
        if not member_settings.role_id:
            return
        if guild_role := guild.get_role(member_settings.role_id):
            await after.remove_roles(guild_role, reason="Configured to remove custom color roles when member unboosts or leaves the server")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # sourcery skip: assign-if-exp, instance-method-first-arg-name, or-if-exp-identity, swap-if-expression
        guild: discord.Guild = member.guild
        settings = await self.get_member_settings(member)
        if not settings.role_id:
            return
        if role := guild.get_role(settings.role_id):
            await role.delete(reason=f"{member} left the server")
            await self.config.member(member).clear()
