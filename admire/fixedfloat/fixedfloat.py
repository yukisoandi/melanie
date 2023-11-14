from __future__ import annotations

import asyncio

import discord
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie

from melanie import footer_gif, get_image_colors2
from melanie.helpers import make_e

from .api import FixedFloatAPI
from .models.currency import FixedFloatCurrency

FF_ICON = "https://cdn.discordapp.com/attachments/928400431137296425/1076612144268840980/icon_whitebg.png"


class FixedFloat(commands.Cog):
    """Create orders with FixedFloat directly in chat."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=879271957, force_registration=True)
        self.ff = FixedFloatAPI("Iw4kSKXnNFdvg81Fblm2hIo801UadfS5LJE3EcHW", "wzld8yqhsqqsjuKqyGtG76h3Q02xaN0lPJWIPR5M")

    async def get_base_price(self, currency: str, ammount=100) -> float:
        data = await self.ff.get_price("USDT", currency, ammount)
        return data["data"]["to"]["amount"]

    async def load_currencies(self) -> list[FixedFloatCurrency]:
        return await self.ff.get_currencies()

    async def check_currency(self, currency: str) -> bool:
        currencies: list[FixedFloatCurrency] = await self.load_currencies()
        currency = currency.upper()
        return any(c.currency == currency for c in currencies)

    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command(aliases=["order"])
    async def fixedfloat(self, ctx: commands.Context, from_currency: str, to_currency: str, to_address: str, amount: float = None):
        """Create a FixedFloat order (without US restriction!).

        If the amount is not specified, we'll create the order for $100
        USD

        """
        from_currency: str = from_currency.upper()
        to_currency: str = to_currency.upper()
        async with ctx.typing():
            async with asyncio.timeout(30):
                if not await self.check_currency(from_currency):
                    return await ctx.send(embed=make_e("That originating currency is invalid", 3))
                if not await self.check_currency(to_currency):
                    return await ctx.send(embed=make_e("The transfer to currency is invalid", 3))
                if not amount:
                    amount: float = await self.get_base_price(from_currency)

                call = await self.ff.create_order(from_currency, to_currency, to_address, amount)
                order = call.data

                embed = discord.Embed()
                embed.title = "FixedFloat Order Created!"

                lookup = await get_image_colors2(FF_ICON)

                if lookup:
                    embed.color = lookup.dominant.decimal

                embed.set_thumbnail(url=FF_ICON)
                url = f"https://fixedfloat.com/order/{order.id}"
                embed.url = url
                embed.add_field(name="Order ID", value=order.id, inline=False)
                embed.add_field(name=f"Send {from_currency} To", value=order.from_.address, inline=False)
                embed.add_field(name=f"Amount (sending {from_currency})", value=str(order.from_.amount))
                embed.add_field(name=f"Receiving Amount ({to_currency})", value=str(order.to.amount))
                embed.add_field(name="Receiving Address", value=order.to.address, inline=False)
                embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                embed.description = f"If the amount of the transaction you sent differs from the initial amount specified in the order with a float rate, the order will be automatically recalculated. Check the order status at [{url}]({url})"
                return await ctx.send(embed=embed)
