import asyncio
import time
from contextlib import suppress
from typing import Union

import discord
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from melanie import cancel_tasks, checkpoint, make_e, spawn_task
from purchases.models import GlobalSettings, UserSale, UserSettings


class Purchases(commands.Cog):
    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.active_tasks: list[asyncio.Task] = []
        self.config = Config.get_conf(self, identifier=123444444, force_registration=True)
        self.config.register_user(**UserSettings().dict())
        self.config.register_global(**GlobalSettings().dict())
        spawn_task(self.validate_purchase_entries(), self.active_tasks)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def validate_purchase_entries(self):
        await self.bot.waits_uptime_for(30)
        all_users = await self.config.all_users()

        for uid, _data in all_users.items():
            await checkpoint()
            data = UserSettings.parse_obj(_data)
            for purchase in data.purchases:
                await checkpoint()
                if not purchase.guild_name and (guild := self.bot.get_guild(purchase.guild_id)):
                    purchase.guild_name = str(guild)

                    await self.set_user_settings(uid, data)

    async def paid_role(self) -> discord.Role | None:
        if guild := self.bot.get_guild(915317604153962546):
            rid = await self.config.paid_role_id()
            return guild.get_role(rid)

    async def get_user_settings(self, user: discord.User) -> UserSettings:
        all_data = await self.config.user(user).all()

        return UserSettings.parse_obj(all_data)

    async def set_user_settings(self, user: discord.User | int, settings: UserSettings):
        user_id = user.id if hasattr(user, "id") else int(user)
        async with self.config.user_from_id(user_id).all() as _data:
            _data.update(settings.dict())

    @commands.command()
    @commands.is_owner()
    async def purchase(self, ctx: commands.Context, member: Union[discord.Member, discord.User], guild_id_or_invite: str, payment_method: str):
        settings = await self.get_user_settings(member)

        invite: None
        guild_id = guild_id_or_invite
        try:
            guild_id = int(guild_id_or_invite)

            guild_name = None
        except ValueError:
            with suppress(discord.HTTPException):
                invite: discord.Invite = await self.bot.fetch_invite(guild_id_or_invite)
                guild_id = invite.guild.id
                guild_name = str(invite.guild)
        sale = settings.get_sale(guild_id)
        if sale:
            return await ctx.send(
                embed=make_e(
                    f"Purchase for guild ID **{guild_id}** has already been created!",
                    2,
                    tip=f'created on {sale.created_at.format("MM-DD")} by {self.bot.get_user(sale.created_by) or sale.created_by}',
                ),
            )
        if not guild_name and (guild := self.bot.get_guild(guild_id)):
            guild_name = str(guild)
        sale = UserSale(
            method=payment_method,
            date=time.time(),
            guild_id=guild_id,
            guild_name=guild_name,
            user_name=str(member),
            monthly=False,
            created_by=ctx.author.id,
        )

        async with ctx.typing(), asyncio.timeout(5):
            settings.purchases.append(sale)
            paid_role = await self.paid_role()
            if paid_role and isinstance(member, discord.Member) and paid_role not in member.roles:
                await member.add_roles(paid_role)
            await self.set_user_settings(member, settings)
            if baron := self.bot.get_cog("Baron"):
                async with baron.config.whitelist() as wl:
                    if sale.guild_id not in wl:
                        wl.append(sale.guild_id)
                await baron.set_redis_data()

            await ctx.send(embed=make_e(f"Purchase recorded for {member.mention} for Guild ID {guild_id}"))

    @commands.command()
    @commands.is_owner()
    async def purchases(self, ctx: commands.Context, user: discord.User):
        settings = await self.get_user_settings(user)
        if not settings.purchases:
            return await ctx.send(embed=make_e(f"No purchases found for **{user.display_name}**", status=2))
        embed = make_e(f"Purchases for {user} ({user.id})", status="info")
        for purchase in settings.purchases:
            guild: discord.Guild = self.bot.get_guild(purchase.guild_id)
            name = str(guild) or purchase.guild_name
            time_str = purchase.created_at.isoformat()
            embed.add_field(
                name=name,
                value=f"Payment Method: {purchase.method}\nPurchase Date: {time_str}\nStaff: {self.bot.get_user(purchase.created_by) or purchase.created_by}",
            )
        await ctx.send(embed=embed)
