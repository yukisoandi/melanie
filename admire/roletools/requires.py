from __future__ import annotations

from melaniebot.core import commands
from melaniebot.core.commands import Context
from melaniebot.core.utils.chat_formatting import humanize_list

from .abc import RoleToolsMixin, roletools
from .converter import RoleHierarchyConverter


def _(x):
    return x


class RoleToolsRequires(RoleToolsMixin):
    """This class handles required role settings."""

    @roletools.group(name="required")
    async def required_roles(self, ctx: Context) -> None:
        """Set role requirements."""

    @required_roles.command(name="add")
    @commands.admin_or_permissions(manage_roles=True)
    async def required_add(self, ctx: Context, role: RoleHierarchyConverter, *required: RoleHierarchyConverter) -> None:
        """Add role requirements.

        `<role>` This is the role a user may acquire you want to set requirements for.
        `<requires>` The role(s) the user requires before being allowed to gain this role.

        Note: This will only work for reaction roles from this cog.

        """
        cur_setting = await self.config.role(role).required()
        for included_role in required:
            if included_role.id not in cur_setting:
                cur_setting.append(included_role.id)
        await self.config.role(role).required.set(cur_setting)
        roles = [ctx.guild.get_role(i) for i in cur_setting]
        role_names = humanize_list([i.mention for i in roles if i])
        await ctx.send(f"The {role.mention} role will now only be given if the following roles are already owned.\n{role_names}.")

    @required_roles.command(name="remove")
    @commands.admin_or_permissions(manage_roles=True)
    async def required_remove(self, ctx: Context, role: RoleHierarchyConverter, *required: RoleHierarchyConverter) -> None:
        """Remove role requirements.

        `<role>` This is the role a user may acquire you want to set requirements for.
        `<requires>` The role(s) you wish to have added when a user gains the `<role>`

        Note: This will only work for reaction roles from this cog.

        """
        cur_setting = await self.config.role(role).required()
        for included_role in required:
            if included_role.id in cur_setting:
                cur_setting.remove(included_role.id)
        await self.config.role(role).required.set(cur_setting)
        if roles := [ctx.guild.get_role(i) for i in cur_setting]:
            role_names = humanize_list([i.mention for i in roles if i])
            await ctx.send(f"The {role.mention} role will now only be given if the following roles are already owned.\n{role_names}.")
        else:
            await ctx.send(f"The {role.mention} role will no longer require any other roles to be added.")
