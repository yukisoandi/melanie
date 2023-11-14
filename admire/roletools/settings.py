from __future__ import annotations

from typing import Optional, Union

from melaniebot.core import bank, commands
from melaniebot.core.commands import Context

from .abc import RoleToolsMixin, roletools
from .converter import RoleHierarchyConverter


def _(x):
    return x


class RoleToolsSettings(RoleToolsMixin):
    """This class handles setting the roletools role settings."""

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def selfadd(self, ctx: Context, true_or_false: Optional[bool] = None, *, role: RoleHierarchyConverter) -> None:
        """Set whether or not a user can apply the role to themselves.

        `[true_or_false]` optional boolean of what to set the setting
        to. If not provided the current setting will be shown instead.
        `<role>` The role you want to set.

        """
        cur_setting = await self.config.role(role).selfassignable()
        if true_or_false is None:
            if cur_setting:
                await ctx.send(f"The {role} role is self assignable.")
            else:
                command = f"`{ctx.clean_prefix}roletools selfadd yes {role.name}`"
                await ctx.send(f"The {role.mention} role is not self assignable. Run the command {command} to make it self assignable.")
            return
        if true_or_false is True:
            await self.config.role(role).selfassignable.set(True)
            await ctx.send(f"The {role.mention} role is now self assignable.")
        if true_or_false is False:
            await self.config.role(role).selfassignable.set(False)
            await ctx.send(f"The {role.mention} role is no longer self assignable.")

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def selfrem(self, ctx: Context, true_or_false: Optional[bool] = None, *, role: RoleHierarchyConverter) -> None:
        """Set whether or not a user can remove the role from themselves.

        `[true_or_false]` optional boolean of what to set the setting
        to. If not provided the current setting will be shown instead.
        `<role>` The role you want to set.

        """
        cur_setting = await self.config.role(role).selfremovable()
        if true_or_false is None:
            if cur_setting:
                await ctx.send(f"The {role.mention} role is self removeable.")
            else:
                command = f"`{ctx.clean_prefix}roletools selfrem yes {role.name}`"
                await ctx.send(f"The {role.mention} role is not self removable. Run the command {command} to make it self removeable.")
            return
        if true_or_false is True:
            await self.config.role(role).selfremovable.set(True)
            await ctx.send(f"The {role.mention} role is now self removeable.")
        if true_or_false is False:
            await self.config.role(role).selfremovable.set(False)
            await ctx.send(f"The {role.mention} role is no longer self removeable.")

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def atomic(self, ctx: Context, true_or_false: Optional[Union[bool, str]] = None) -> None:
        """Set the atomicity of role assignment. What this means is that when this
        is `True` roles will be applied inidvidually and not cause any errors.
        When this is set to `False` roles will be grouped together into one
        call.

        This can cause race conditions if you have other methods of
        applying roles setup when set to `False`.

        `[true_or_false]` optional boolean of what to set the setting
        to. To reset back to the default global rules use `clear`. If
        not provided the current setting will be shown instead.

        """
        cur_setting = await self.config.guild(ctx.guild).atomic()
        if true_or_false is None or true_or_false not in ["clear", True, False]:
            if cur_setting is True:
                msg = "This server is currently using atomic role assignment"
            elif cur_setting is False:
                msg = "This server is not currently using atomic role assignment."
            else:
                msg = f"This server currently using the global atomic role assignment setting `{await self.config.atomic()}`."
            command = f"`{ctx.clean_prefix}roletools atomic yes`"
            cmd_msg = f"Do {command} to atomically assign roles."
            await ctx.send(f"{msg} {cmd_msg}")
            return
        elif true_or_false is True:
            await self.config.guild(ctx.guild).atomic.set(True)
            await ctx.send("RoleTools will now atomically assign roles.")
        elif true_or_false is False:
            await self.config.guild(ctx.guild).atomic.set(False)
            await ctx.send("RoleTools will no longer atomically assign roles.")
        else:
            await self.config.guild(ctx.guild).atomic.clear()
            await ctx.send("RoleTools will now default to the global atomic setting.")

    @roletools.command()
    @commands.is_owner()
    async def globalatomic(self, ctx: Context, true_or_false: Optional[bool] = None) -> None:
        """Set the atomicity of role assignment. What this means is that when this
        is `True` roles will be applied inidvidually and not cause any errors.
        When this is set to `False` roles will be grouped together into one
        call.

        This can cause race conditions if you have other methods of
        applying roles setup when set to `False`.

        `[true_or_false]` optional boolean of what to set the setting
        to. If not provided the current setting will be shown instead.

        """
        cur_setting = await self.config.atomic()
        if true_or_false is None:
            if cur_setting:
                await ctx.send("I am currently using atomic role assignment")
            else:
                command = f"`{ctx.clean_prefix}roletools globalatomic yes`"
                await ctx.send(f"I am not currently using atomic role assignment. Do {command} to atomically assign roles.")
            return
        if true_or_false is True:
            await self.config.atomic.clear()
            await ctx.send("RoleTools will now atomically assign roles.")
        if true_or_false is False:
            await self.config.atomic.set(False)
            await ctx.send("RoleTools will no longer atomically assign roles.")

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def cost(self, ctx: Context, cost: Optional[int] = None, *, role: RoleHierarchyConverter) -> None:
        """Set whether or not a user can remove the role from themselves.

        `[cost]` The price you want to set the role at in bot credits.
        Setting this to 0 or lower will remove the cost. If not provided
        the current setting will be shown instead. `<role>` The role you
        want to set.

        """
        if await bank.is_global() and not await self.bot.is_owner(ctx.author):
            await ctx.send("This command is locked to bot owner only while the bank is set to global.")
            return
        if cost is not None and cost >= await bank.get_max_balance(ctx.guild):
            await ctx.send("You cannot set a cost higher than the maximum credits balance.")
            return

        cur_setting = await self.config.role(role).cost()
        currency_name = await bank.get_currency_name(ctx.guild)
        if cost is None:
            if cur_setting:
                await ctx.send(f"The role {role} currently costs {cost} {currency_name}.")
            else:
                command = f"`{ctx.clean_prefix} roletools cost SOME_NUMBER {role.name}`"
                await ctx.send(
                    f"The role {role.mention} does not currently cost any {currency_name}. Run the command {command} to allow this role to require credits.",
                )
            return
        else:
            if cost <= 0:
                await self.config.role(role).cost.clear()
                await ctx.send(f"The {role.mention} will not require any {currency_name} to acquire.")
                return
            else:
                await self.config.role(role).cost.set(cost)
                await ctx.send(f"The {role.mention} will now cost {cost} {currency_name} to acquire.")

    @roletools.command()
    @commands.admin_or_permissions(manage_roles=True)
    async def sticky(self, ctx: Context, true_or_false: Optional[bool] = None, *, role: RoleHierarchyConverter) -> None:
        """Set whether or not a role will be re-applied when a user leaves and
        rejoins the server.

        `[true_or_false]` optional boolean of what to set the setting
        to. If not provided the current setting will be shown instead.
        `<role>` The role you want to set.

        """
        cur_setting = await self.config.role(role).sticky()
        if true_or_false is None:
            if cur_setting:
                await ctx.send(f"The {role.mention} role is sticky.")
            else:
                command = f"{ctx.clean_prefix}roletools sticky yes {role.name}"
                await ctx.send(f"The {role.mention} role is not sticky. Run the command {command} to make it sticky.")
            return
        if true_or_false is True:
            await self.config.role(role).sticky.set(True)
            await ctx.send(f"The {role.mention} role is now sticky.")
        if true_or_false is False:
            await self.config.role(role).sticky.set(False)
            await ctx.send(f"The {role.mention} role is no longer sticky.")

    @roletools.command(aliases=["auto"])
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole(self, ctx: Context, true_or_false: Optional[bool] = None, *, role: RoleHierarchyConverter) -> None:
        """Set a role to be automatically applied when a user joins the server.

        `[true_or_false]` optional boolean of what to set the setting
        to. If not provided the current setting will be shown instead.
        `<role>` The role you want to set.

        """
        cur_setting = await self.config.role(role).auto()
        if true_or_false is None:
            if cur_setting:
                await ctx.send(f"The role {role} is automatically applied on joining.")
            else:
                command = f"`{ctx.clean_prefix}roletools auto yes {role.name}`"
                await ctx.send(
                    f"The {role.mention} role is not automatically applied when a member joins  this server. Run the command {command} to make it automatically apply when a user joins.",
                )
            return
        if true_or_false is True:
            async with self.config.guild(ctx.guild).auto_roles() as current_roles:
                if role.id not in current_roles:
                    current_roles.append(role.id)
                if ctx.guild.id not in self.settings:
                    self.settings[ctx.guild.id] = await self.config.guild(ctx.guild).all()
                if role.id not in self.settings[ctx.guild.id]["auto_roles"]:
                    self.settings[ctx.guild.id]["auto_roles"].append(role.id)
            await self.config.role(role).auto.set(True)
            await ctx.send(f"The {role.mention} role will now automatically be applied when a user joins.")
        if true_or_false is False:
            async with self.config.guild(ctx.guild).auto_roles() as current_roles:
                if role.id in current_roles:
                    current_roles.remove(role.id)
                if ctx.guild.id in self.settings and role.id in self.settings[ctx.guild.id]["auto_roles"]:
                    self.settings[ctx.guild.id]["auto_roles"].remove(role.id)
            await self.config.role(role).auto.set(False)
            await ctx.send(f"The {role.mention} role will not automatically be applied when a user joins.")
