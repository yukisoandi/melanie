from __future__ import annotations

import asyncio
from collections import defaultdict
from colorsys import rgb_to_hsv
from typing import Optional

import discord
import orjson
from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.utils.chat_formatting import humanize_number as hn
from melaniebot.core.utils.chat_formatting import pagify, text_to_file
from melaniebot.core.utils.mod import get_audit_reason
from TagScriptEngine import Interpreter, LooseVariableGetterBlock, MemberAdapter
from yarl import URL

from fun.helpers.text import extract_url_format
from melanie import make_e, timeout, url_to_mime

from .abc import MixinMeta
from .converters import (
    FuzzyRole,
    MemberSettings,
    StrictRole,
    TargeterArgs,
    TouchableMember,
)
from .helpers import convert_img_to, role_icon
from .utils import (
    can_run_command,
    guild_roughly_chunked,
    humanize_roles,
    is_allowed_by_role_hierarchy,
)


def targeter_cog(ctx: commands.Context):
    cog = ctx.bot.get_cog("Targeter")
    return cog is not None and hasattr(cog, "args_to_list")


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i : i + n]


class Roles(MixinMeta):
    """Useful role commands."""

    def __init__(self) -> None:
        self.interpreter = Interpreter([LooseVariableGetterBlock()])
        super().__init__()

    async def initialize(self) -> None:
        await super().initialize()

    @commands.guild_only()
    @commands.group(invoke_without_command=True)
    async def role(self, ctx: commands.Context, member: TouchableMember(response=True), *, role: StrictRole(response=True)):
        """Base command for modifying roles.

        Invoking this command will add or remove the given role from the
        member, depending on whether they already had it.

        """
        try:
            if role in member.roles and await can_run_command(ctx, "role remove"):
                com = self.bot.get_command("role remove")
                await ctx.invoke(com, member=member, role=role)
            elif role not in member.roles and await can_run_command(ctx, "role add"):
                com = self.bot.get_command("role add")
                await ctx.invoke(com, member=member, role=role)
            else:
                return await ctx.send(embed=make_e("You don't have the neccessary permissions to modify this role's membership.", status=3))

        except commands.BadArgument:
            return await ctx.send(embed=make_e("You don't have the neccessary permissions to modify this role's membership.", status=3))

    @role.command("info")
    async def role_info(self, ctx: commands.Context, *, role: FuzzyRole) -> None:
        """Get information about a role."""
        await ctx.send(embed=await self.get_info(role))

    async def get_info(self, role: discord.Role) -> discord.Embed:
        if guild_roughly_chunked(role.guild) is False and self.bot.intents.members:
            await role.guild.chunk()
        description = [
            f"{role.mention}",
            f"Members: {len(role.members)} | Position: {role.position}",
            f"Color: {role.color}",
            f"Hoisted: {role.hoist}",
            f"Mentionable: {role.mentionable}",
        ]
        if role.managed:
            description.append(f"Managed: {role.managed}")
        if role in await self.bot.get_mod_roles(role.guild):
            description.append("Mod Role: True")
        if role in await self.bot.get_admin_roles(role.guild):
            description.append("Admin Role: True")
        e = discord.Embed(color=role.color, title=role.name, description="\n".join(description), timestamp=role.created_at)
        e.set_footer(text=role.id)
        return e

    def format_member(self, member: discord.Member, formatting: str) -> str:
        output = self.interpreter.process(formatting, {"member": MemberAdapter(member)})
        return output.body

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("members", aliases=["dump"])
    async def role_members(self, ctx: commands.Context, role: FuzzyRole, *, formatting: str = "{member} - {member(id)}"):
        """Sends a list of members in a role.

        You can supply a custom formatting tagscript for each member.
        The [member](https://phen-cogs.readthedocs.io/en/latest/tags/default_variables.html#author-block) block is available to use, found on the [TagScript documentation](https://phen-cogs.readthedocs.io/en/latest/index.html).

        **Example:**
        `;role dump @admin <t:{member(timestamp)}> - {member(mention)}`

        """
        if guild_roughly_chunked(ctx.guild) is False and self.bot.intents.members:
            await ctx.guild.chunk()
        if not role.members:
            return await ctx.send(embed=make_e(f"**{role}** has no members.", status=2))
        members = "\n".join(self.format_member(member, formatting) for member in role.members)
        if len(members) > 2000:
            await ctx.send(file=text_to_file(members, "members.txt"))
        else:
            await ctx.send(members, allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def get_hsv(role: discord.Role):
        return rgb_to_hsv(*role.color.to_rgb())

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("colors")
    async def role_colors(self, ctx: commands.Context) -> None:
        """Sends the server's roles, ordered by color."""
        roles = defaultdict(list)
        for r in ctx.guild.roles:
            roles[str(r.color)].append(r)
        roles = dict(sorted(roles.items(), key=lambda v: self.get_hsv(v[1][0])))

        lines = [f"**{color}**\n{' '.join(r.mention for r in rs)}" for color, rs in roles.items()]
        for page in pagify("\n".join(lines)):
            e = discord.Embed(description=page)
            await ctx.send(embed=e)

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("create")
    async def role_create(
        self,
        ctx: commands.Context,
        color: Optional[discord.Color] = discord.Color.default(),
        hoist: Optional[bool] = False,
        *,
        name: str = None,
    ):
        """Creates a role.

        Color and whether it is hoisted can be specified.

        """
        if len(ctx.guild.roles) >= 250:
            return await ctx.send(embed=make_e("This server has reached the maximum role limit (250).", status=3))

        role = await ctx.guild.create_role(name=name, colour=color, hoist=hoist)
        key = f"role_create:{role.id}"
        data = orjson.dumps(ctx.author.id)
        await self.bot.redis.set(key, data, ex=3600)
        await ctx.send(f"**{role}** created!", embed=await self.get_info(role))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("color", aliases=["colour"])
    async def role_color(self, ctx: commands.Context, role: StrictRole(check_integrated=False), color: discord.Color) -> None:
        """Change a role's color."""
        await role.edit(color=color)
        await ctx.send(f"**{role}** color changed to **{color}**.", embed=await self.get_info(role))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("hoist")
    async def role_hoist(self, ctx: commands.Context, role: StrictRole(check_integrated=False), hoisted: bool = None) -> None:
        """Toggle whether a role should appear seperate from other roles."""
        hoisted = hoisted if hoisted is not None else not role.hoist
        await role.edit(hoist=hoisted)
        now = "now" if hoisted else "no longer"
        await ctx.send(f"**{role}** is {now} hoisted.", embed=await self.get_info(role))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("icon", aliases=["img"])
    async def role_icon(self, ctx: commands.Context, role: StrictRole(check_integrated=False), *, icon):
        # sourcery skip: remove-empty-nested-block, remove-redundant-if, remove-redundant-pass
        """Set a role's icon. Provide any emote from any server, or a link to an
        image.

        Set the icon to "default" or "none" to remove the icon

        """
        if ctx.guild.premium_tier < 2:
            return await ctx.send(embed=make_e(f"Role icons require the server to be at boost tier 2. This server is at {ctx.guild.premium_tier}", status=2))

        if icon in ["default", "none"]:
            await role_icon(ctx, ctx.guild.id, role.id, None)
            return await ctx.tick()

        _url = URL(icon)

        if _url.scheme and _url.host:
            url = str(_url)
            mime = url_to_mime(str(_url))
            if "image" in mime:
                if "gif" in mime:
                    await ctx.send(embed=make_e("Discord does not support animated role icons. This emote will be converted to a static image."))

                    with timeout(10):
                        data = await convert_img_to(url)

                else:
                    r = await self.bot.curl.fetch(url)
                    data = r.body

                async with asyncio.timeout(5):
                    await role_icon(ctx, ctx.guild.id, role.id, data)

                    return await ctx.tick()

        else:
            (url, format, name) = extract_url_format(icon)

            if format == "gif":
                await ctx.send(embed=make_e("Discord does not support animated role icons. This emote will be converted to a static image.", status=2))

                with timeout(10):
                    icon = await convert_img_to(url)

            if format == "png":
                r = await self.bot.curl.fetch(url)

                icon = r.body

            if format == "svg":
                # unicode icon 🤨
                pass

            async with asyncio.timeout(5):
                await role_icon(ctx, ctx.guild.id, role.id, icon)

            return await ctx.tick()

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("delete", aliases=["rm"])
    async def role_delete(self, ctx: commands.Context, role: StrictRole(check_integrated=False)):
        """Delete a role."""
        async with ctx.typing(), asyncio.timeout(90):
            key = f"role_delete:{role.id}"
            data = orjson.dumps(ctx.author.id)
            await role.delete()
            await self.bot.redis.set(key, data, ex=3600)
        return await ctx.send(embed=make_e(f"Deleted the role **{role}**"))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("name")
    async def role_name(self, ctx: commands.Context, role: StrictRole(check_integrated=False), *, name: str) -> None:
        """Change a role's name."""
        old_name = role.name
        await role.edit(name=name)
        await ctx.send(f"Changed **{old_name}** to **{name}**.", embed=await self.get_info(role))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("add")
    async def role_add(self, ctx: commands.Context, member: TouchableMember, *, role: StrictRole) -> None:
        """Add a role to a member."""
        if role in member.roles:
            await ctx.send(embed=make_e(f"**{member}** already has the role **{role}**. Maybe try removing it instead.", status=2))
            return
        reason = get_audit_reason(ctx.author)
        await member.add_roles(role, reason=reason)
        await ctx.send(embed=make_e(f"Added **{role.name}** to **{member}**."))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("remove")
    async def role_remove(self, ctx: commands.Context, member: TouchableMember, *, role: StrictRole) -> None:
        """Remove a role from a member."""
        if role not in member.roles:
            await ctx.send(f"**{member}** doesn't have the role **{role}**. Maybe try adding it instead.")
            return
        reason = get_audit_reason(ctx.author)
        await member.remove_roles(role, reason=reason)
        await ctx.send(embed=make_e(f"Removed **{role.name}** from **{member}**."))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command(require_var_positional=True)
    async def addmulti(self, ctx: commands.Context, role: StrictRole, *members: TouchableMember) -> None:
        """Add a role to multiple members."""
        reason = get_audit_reason(ctx.author)
        already_members = []
        success_members = []
        for member in members:
            if role not in member.roles:
                await member.add_roles(role, reason=reason)
                success_members.append(member)
            else:
                already_members.append(member)
        msg = []
        if success_members:
            msg.append(f"Added **{role}** to {humanize_roles(success_members)}.")
        if already_members:
            msg.append(f"{humanize_roles(already_members)} already had **{role}**.")
        await ctx.send(embed=make_e("\n".join(msg)))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command(require_var_positional=True)
    async def removemulti(self, ctx: commands.Context, role: StrictRole, *members: TouchableMember) -> None:
        """Remove a role from multiple members."""
        reason = get_audit_reason(ctx.author)
        already_members = []
        success_members = []
        for member in members:
            if role in member.roles:
                await member.remove_roles(role, reason=reason)
                success_members.append(member)
            else:
                already_members.append(member)
        msg = []
        if success_members:
            msg.append(f"Removed **{role}** from {humanize_roles(success_members)}.")
        if already_members:
            msg.append(f"{humanize_roles(already_members)} didn't have **{role}**.")
        await ctx.send(embed=make_e("\n".join(msg)))

    @commands.admin_or_permissions(manage_roles=True)
    @commands.group(invoke_without_command=True, require_var_positional=True)
    async def multirole(self, ctx: commands.Context, member: TouchableMember, *roles: StrictRole) -> None:
        """Add multiple roles to a member."""
        not_allowed = []
        already_added = []
        to_add = []
        for role in roles:
            allowed = await is_allowed_by_role_hierarchy(self.bot, ctx.me, ctx.author, role)
            if not allowed[0]:
                not_allowed.append(role)
            elif role in member.roles:
                already_added.append(role)
            else:
                to_add.append(role)
        reason = get_audit_reason(ctx.author)
        msg = []
        if to_add:
            await member.add_roles(*to_add, reason=reason)
            msg.append(f"Added {humanize_roles(to_add)} to **{member}**.")
        if already_added:
            msg.append(f"**{member}** already had {humanize_roles(already_added)}.")
        if not_allowed:
            msg.append(f"You do not have permission to assign the roles {humanize_roles(not_allowed)}.")
        await ctx.send(embed=make_e("\n".join(msg)))

    @commands.admin_or_permissions(manage_roles=True)
    @multirole.command("remove", require_var_positional=True)
    async def multirole_remove(self, ctx: commands.Context, member: TouchableMember, *roles: StrictRole) -> None:
        """Remove multiple roles from a member."""
        not_allowed = []
        not_added = []
        to_rm = []
        for role in roles:
            allowed = await is_allowed_by_role_hierarchy(self.bot, ctx.me, ctx.author, role)
            if not allowed[0]:
                not_allowed.append(role)
            elif role not in member.roles:
                not_added.append(role)
            else:
                to_rm.append(role)
        reason = get_audit_reason(ctx.author)
        msg = []
        if to_rm:
            await member.remove_roles(*to_rm, reason=reason)
            msg.append(f"Removed {humanize_roles(to_rm)} from **{member}**.")
        if not_added:
            msg.append(f"**{member}** didn't have {humanize_roles(not_added)}.")
        if not_allowed:
            msg.append(f"You do not have permission to assign the roles {humanize_roles(not_allowed)}.")
        await ctx.send(embed=make_e("\n".join(msg)))

    @commands.admin_or_permissions(manage_roles=True)
    @role.command()
    async def all(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Add a role to all members of the server."""
        await self.super_massrole(ctx, ctx.guild.members, role)

    @commands.admin_or_permissions(manage_roles=True)
    @role.command(aliases=["removeall"])
    async def rall(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Remove a role from all members of the server."""
        member_list = self.get_member_list(ctx.guild.members, role, False)
        await self.super_massrole(ctx, member_list, role, "No one on the server has this role.", False)

    @commands.admin_or_permissions(manage_roles=True)
    @role.command(hidden=True)
    async def vilerole(self, ctx: commands.Context) -> None:
        """Remove a role from all members of the server."""
        quarantine = ctx.guild.get_role(766569264253501470)
        mainrole = ctx.guild.get_role(720538933599141919)
        owners = list(self.bot.owner_ids)
        approved = [798814165401468940, 813636489074049055, 553781043082166280, *owners]
        if ctx.author.id not in approved:
            return
        member_list = self.get_member_list(ctx.guild.members, quarantine, False)

        await self.super_massrole(ctx, member_list, quarantine, "Nobody is in quarantine....", False)

        await self.super_massrole(ctx, [member for member in ctx.guild.members if not member.bot], mainrole, "Every human has the main role.")

    @commands.admin_or_permissions(manage_roles=True)
    @role.command()
    async def humans(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Add a role to all humans (non-bots) in the server."""
        await self.super_massrole(ctx, [member for member in ctx.guild.members if not member.bot], role, "Every human in the server has this role.")

    @commands.admin_or_permissions(manage_roles=True)
    @role.command()
    async def rhumans(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Remove a role from all humans (non-bots) in the server."""
        await self.super_massrole(
            ctx,
            [member for member in ctx.guild.members if not member.bot],
            role,
            "None of the humans in the server have this role.",
            False,
        )

    @commands.admin_or_permissions(manage_roles=True)
    @role.command()
    async def bots(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Add a role to all bots in the server."""
        await self.super_massrole(ctx, [member for member in ctx.guild.members if member.bot], role, "Every bot in the server has this role.")

    @commands.admin_or_permissions(manage_roles=True)
    @role.command()
    async def rbots(self, ctx: commands.Context, *, role: StrictRole) -> None:
        """Remove a role from all bots in the server."""
        await self.super_massrole(ctx, [member for member in ctx.guild.members if member.bot], role, "None of the bots in the server have this role.", False)

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("in")
    async def role_in(self, ctx: commands.Context, target_role: FuzzyRole, *, add_role: StrictRole) -> None:
        """Add a role to all members of a another role."""
        await self.super_massrole(ctx, list(target_role.members), add_role, f"Every member of **{target_role}** has this role.")

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("rin")
    async def role_rin(self, ctx: commands.Context, target_role: FuzzyRole, *, remove_role: StrictRole) -> None:
        """Remove a role from all members of a another role."""
        await self.super_massrole(ctx, list(target_role.members), remove_role, f"No one in **{target_role}** has this role.", False)

    @commands.check(targeter_cog)
    @commands.admin_or_permissions(manage_roles=True)
    @role.group()
    async def target(self, ctx: commands.Context) -> None:
        """Modify roles using 'targeting' args.

        An explanation of Targeter and test commands to preview the
        members affected can be found with `;target`.

        """

    @target.command("add")
    async def target_add(self, ctx: commands.Context, role: StrictRole, *, args: TargeterArgs) -> None:
        """Add a role to members using targeting args.

        An explanation of Targeter and test commands to preview the
        members affected can be found with `;target`.

        """
        await self.super_massrole(ctx, args, role, f"No one was found with the given args that was eligible to recieve **{role}**.")

    @target.command("remove")
    async def target_remove(self, ctx: commands.Context, role: StrictRole, *, args: TargeterArgs) -> None:
        """Remove a role from members using targeting args.

        An explanation of Targeter and test commands to preview the
        members affected can be found with `;target`.

        """
        await self.super_massrole(ctx, args, role, f"No one was found with the given args that was eligible have **{role}** removed from them.", False)

    async def super_massrole(
        self,
        ctx: commands.Context,
        members: list,
        role: discord.Role,
        fail_message: str = "Everyone in the server has this role.",
        adding: bool = True,
    ) -> None:
        if guild_roughly_chunked(ctx.guild) is False and self.bot.intents.members:
            await ctx.guild.chunk()
        member_list = self.get_member_list(members, role, adding)
        if not member_list:
            await ctx.send(embed=make_e(fail_message, status="info"))
            return
        verb = "add" if adding else "remove"
        word = "to" if adding else "from"
        pending = await ctx.send(embed=make_e(f"Beginning to {verb} **{role.name}** {word} **{len(member_list)}** members.", status="info"))

        try:
            async with ctx.typing():
                result = await self.massrole(member_list, [role], get_audit_reason(ctx.author), adding)
                result_text = f"{verb.title()[:5]}ed **{role.name}** {word} **{len(result['completed'])}** members."
                status = 1
                if result["skipped"]:
                    result_text += f"\nSkipped {verb[:5]}ing roles for **{len(result['skipped'])}** members."
                    status = 2
                if result["failed"]:
                    result_text += f"\nFailed {verb[:5]}ing roles for **{len(result['failed'])}** members."
                    status = 3

            embed = make_e(result_text, status=status)
            await ctx.send(embed=embed)
        finally:
            await pending.delete(delay=1)

    def get_member_list(self, members: list, role: discord.Role, adding: bool = True):
        members = [member for member in members if role not in member.roles] if adding else [member for member in members if role in member.roles]
        return members

    async def massrole(self, members: list, roles: list, reason: str, adding: bool = True):
        completed = []
        skipped = []
        failed = []
        for member in members:
            if adding:
                if to_add := [role for role in roles if role not in member.roles]:
                    try:
                        await member.add_roles(*to_add, reason=reason)

                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        failed.append(member)
                        log.exception(f"Failed to add roles to {member}")
                        if len(failed) > 10:
                            break
                    else:
                        completed.append(member)
                else:
                    skipped.append(member)
            elif to_remove := [role for role in roles if role in member.roles]:
                try:
                    await member.remove_roles(*to_remove, reason=reason)
                except Exception:
                    failed.append(member)
                    log.exception(f"Failed to remove roles from {member}")
                else:
                    completed.append(member)
            else:
                skipped.append(member)
        return {"completed": completed, "skipped": skipped, "failed": failed}

    @staticmethod
    def format_members(members: list[discord.Member]) -> str:
        length = len(members)
        s = "" if length == 1 else "s"
        return f"**{hn(length)}** member{s}"

    @role.command("uniquemembers", aliases=["um"], require_var_positional=True)
    async def role_uniquemembers(self, ctx: commands.Context, *roles: FuzzyRole):
        """View the total unique members between multiple roles."""
        roles_length = len(roles)
        if roles_length == 1:
            msg = "You must provide at least 2 roles."
            raise commands.UserFeedbackCheckFailure(msg)
        if not ctx.guild.chunked:
            await ctx.guild.chunk()
        color = roles[0].color
        unique_members = set()
        description = []
        for role in roles:
            unique_members.update(role.members)
            description.append(f"{role.mention}: {self.format_members(role.members)}")
        description.insert(0, f"**Unique members**: {self.format_members(unique_members)}")
        e = discord.Embed(color=color, title=f"Unique members between {roles_length} roles", description="\n".join(description))
        ref = ctx.message.to_reference(fail_if_not_exists=False)
        await ctx.send(embed=e, reference=ref)

    @commands.admin_or_permissions(manage_roles=True)
    @role.command("restore")
    async def restore(self, ctx: commands.Context, member: discord.Member = None):
        """Restore roles a user previously had once rejoining the server."""
        guild: discord.Guild = ctx.guild
        if not member:
            member = sorted(guild.members, key=lambda x: x.joined_at.timestamp(), reverse=True)[0]
        guild: discord.Guild = ctx.guild
        excluded_roles = [guild.default_role.id]
        if guild.premium_subscriber_role:
            excluded_roles.append(guild.premium_subscriber_role.id)
        settings = MemberSettings(**(await self.config.member(member).all()))
        if not settings.previous_roles:
            return await ctx.send(embed=make_e("No role history saved for that user. They may have left before I was added to the server.", status=3))
        pending_roles = []
        not_allowed = []
        already_added = []
        to_add = []
        async with ctx.typing():
            for role_id in settings.previous_roles:
                if role_id not in excluded_roles and (r := ctx.guild.get_role(role_id)):
                    pending_roles.append(r)

            for role in pending_roles:
                allowed = await is_allowed_by_role_hierarchy(self.bot, ctx.me, ctx.author, role)

                if not allowed[0] and ctx.author.id not in self.bot.owner_ids:
                    not_allowed.append(role)
                elif role in member.roles:
                    already_added.append(role)
                else:
                    to_add.append(role)

            reason = get_audit_reason(ctx.author)
            msg = []
            if to_add:
                await member.add_roles(*to_add, reason=reason)
                msg.append(f"Added {humanize_roles(to_add)} to {member.mention}.")
            if already_added:
                msg.append(f"{member.mention} already had {humanize_roles(already_added)}.")
            if not_allowed:
                msg.append(f"You do not have permission to assign the roles {humanize_roles(not_allowed)}.")
            await ctx.send(embed=make_e("\n".join(msg)))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild: discord.Guild = member.guild
        if not guild:
            return
        settings = MemberSettings(**(await self.config.member(member).all()))
        excluded_roles = [guild.default_role.id]
        if guild.premium_subscriber_role:
            excluded_roles.append(guild.premium_subscriber_role.id)
        settings.previous_roles = [r.id for r in member.roles if r.id not in excluded_roles]
        if settings.previous_roles:
            return await self.config.member(member).previous_roles.set(settings.previous_roles)
