from __future__ import annotations

import datetime
from typing import Optional, Union

import discord
from melaniebot.core import commands
from melaniebot.core.bot import Melanie

from melanie import log

from .coin import Coin, CoinBase
from .errors import CoinMarketCapError


class Conversions(commands.Cog):
    """Gather information about various crypto currencies, stocks, and converts to
    different currencies.
    """

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot

        self.coin_index: dict[int, CoinBase] = {}

    @property
    def session(self):
        return self.bot.aio

    @commands.command(aliases=["bitcoin"])
    async def btc(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from BTC to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "BTC", amount, currency, full)

    @commands.command(aliases=["ethereum"])
    async def eth(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from ETH to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "ETH", amount, currency, full)

    @commands.command(aliases=["litecoin"])
    async def ltc(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from LTC to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "LTC", amount, currency, full)

    @commands.command(aliases=["monero"])
    async def xmr(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from XMR to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "XMR", amount, currency, full)

    @commands.command(aliases=["bitcoin-cash"])
    async def bch(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from BCH to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "BCH", amount, currency, full)

    @commands.command(aliases=["dogecoin"])
    async def doge(self, ctx: commands.Context, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Converts from XDG to a given currency.

        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        await ctx.invoke(self.crypto, "DOGE", amount, currency, full)

    async def get_header(self) -> Optional[dict[str, str]]:
        api_key = (await self.bot.get_shared_api_tokens("coinmarketcap")).get("api_key")
        return {"X-CMC_PRO_API_KEY": api_key} if api_key else None

    async def get_coins(self, coins: list[str]) -> list[Coin]:
        if not self.coin_index:
            await self.checkcoins()
        to_ret = []
        coin_ids = []
        for search_coin in coins:
            coin_ids.extend(str(_id) for _id, coin in self.coin_index.items() if search_coin.upper() == coin.symbol or search_coin.lower() == coin.name.lower())

        params = {"id": ",".join(coin_ids)}
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        async with self.session.get(url, headers=await self.get_header(), params=params) as resp:
            data = await resp.json()
            coins_data = data.get("data", {})
            to_ret.extend(Coin.from_json(coin_data) for coin_id, coin_data in coins_data.items())

        return to_ret

    async def get_latest_coins(self) -> list[Coin]:
        """This converts all latest coins into Coin objects for us to use."""
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        async with self.session.get(url, headers=await self.get_header()) as resp:
            data = await resp.json()
            if resp.status == 200:
                return [Coin.from_json(c) for c in data["data"]]
            elif resp.status == 401:
                msg = "Reported error "
                raise CoinMarketCapError(msg)
            else:
                msg = f"Something went wrong, the error code is {resp.status}\n`{data['error_message']}`"
                raise CoinMarketCapError(msg)

    async def checkcoins(self) -> None:
        if self.coin_index:
            return
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
        async with self.session.get(url, headers=await self.get_header()) as resp:
            data = await resp.json()
        if resp.status == 200:
            self.coin_index = {c["id"]: CoinBase.from_json(c) for c in data.get("data", [])}
        elif resp.status == 401:
            msg = "The bot owner has not set an API key. Please use `{prefix}cryptoapi` to see how to create and setup an API key."
            raise CoinMarketCapError(msg)
        else:
            msg = f"Something went wrong, the error code is {resp.status}\n`{data['error_message']}`"
            raise CoinMarketCapError(msg)

    @commands.command()
    async def multicoin(self, ctx: commands.Context, *coins: str) -> None:
        """Gets the current USD value for a list of coins.

        `coins` must be a list of white space separated crypto coins
        e.g. `;multicoin BTC BCH LTC ETH DASH XRP`

        """
        if not coins:
            try:
                coin_list = await self.get_latest_coins()
            except CoinMarketCapError as e:
                await ctx.send(str(e).replace("{prefix}", ctx.clean_prefix))
                return
        else:
            coin_list = await self.get_coins(coins)
        if not coin_list:
            await ctx.send("The provided list of coins aren't acceptable.")

        if await ctx.embed_requested():
            embed = discord.Embed(title="Crypto coin comparison")
            for coin in coin_list[:25]:
                price = coin.quote["USD"].price
                msg = f"1 {coin.symbol} is {price:,.2f} USD"
                embed.add_field(name=coin.name, value=msg)
            await ctx.send(embed=embed)
        else:
            msg = ""
            for coin in coin_list[:25]:
                price = coin.quote["USD"].price
                msg = f"1 {coin.symbol} is {price:,.2f} USD"
                embed.add_field(name=coin.name, value=msg)
            await ctx.send(msg)

    @commands.command()
    async def crypto(self, ctx: commands.Context, coin: str, amount: float = 1.0, currency: str = "USD", full: Optional[bool] = None) -> None:
        """Displays the latest information about a specified crypto currency.

        `<coin>` must be the name or symbol of a crypto coin
        `[ammount=1.0]` The number of coins you want to know the price
        for. `[currency=USD]` The optional desired currency price.
        Defaults to USD. `[full=True]` is a True/False value whether to
        display just the converted amount or the full display for the
        currency

        """
        async with ctx.typing():
            if full is None and amount == 1.0:
                embed = await self.crypto_embed(ctx, coin, amount, currency, True)
            elif full is None:
                embed = await self.crypto_embed(ctx, coin, amount, currency, False)
            else:
                embed = await self.crypto_embed(ctx, coin, amount, currency, full)
        if embed is None:
            return
        if await ctx.embed_requested():
            await ctx.send(embed=embed["embed"])
        else:
            await ctx.send(embed["content"])

    async def crypto_embed(
        self,
        ctx: commands.Context,
        coin_name: str,
        amount: float,
        currency: str,
        full: Optional[bool],
    ) -> Optional[dict[str, Union[discord.Embed, str]]]:
        """Creates the embed for the crypto currency.

        Parameters
        ----------
            ctx: commands.Context
                Used to return an error message should one happen.
            coin_name: str
                The name of the coin you want to pull information for.
            amount: float
                The amount of coins you want to see the price for.
            currency: str
                The ISO 4217 Currency Code you want the coin converted into.
            full: Optional[bool]
                Whether or not to display full information or just the conversions.

        Returns
        -------
            Optional[Dict[str, Union[discord.Embed, str]]]
                A dictionary containing both the plaintext and discord Embed object
                used for later determining if we can post the embed and if not
                we still have the plaintext available.

        """
        currency = currency.upper()
        if len(currency) > 3 or len(currency) < 3:
            currency = "USD"
        try:
            coins = await self.get_coins([coin_name])
            coin = next(iter(coins), None)
        except CoinMarketCapError as e:
            await ctx.send(str(e).replace("{prefix}", ctx.clean_prefix))
            return None
        if coin is None:
            await ctx.send(f"{coin_name} does not appear to be in my list of coins.")
            return None

        coin_colour = {
            "Bitcoin": discord.Colour.gold(),
            "Bitcoin Cash": discord.Colour.orange(),
            "Ethereum": discord.Colour.dark_grey(),
            "Litecoin": discord.Colour.dark_grey(),
            "Monero": discord.Colour.orange(),
        }
        price = float(coin.quote["USD"].price) * amount
        market_cap = float(coin.quote["USD"].market_cap)
        volume_24h = float(coin.quote["USD"].volume_24h)
        coin_image = f"https://s2.coinmarketcap.com/static/img/coins/128x128/{coin.id}.png"
        coin_url = f"https://coinmarketcap.com/currencies/{coin.id}"
        if currency.upper() != "USD":
            conversionrate = await self.conversionrate("USD", currency)
            if conversionrate:
                price = conversionrate * price
                market_cap *= conversionrate
                volume_24h *= conversionrate

        msg = f"{amount} {coin.symbol} is **{price:,.2f} {currency}**\n"
        embed = discord.Embed(description=msg, colour=coin_colour.get(coin.name, discord.Colour.dark_grey()))
        embed.set_footer(text="As of")
        embed.set_author(name=coin.name, url=coin_url, icon_url=coin_image)
        embed.timestamp = coin.last_updated
        if full:
            hour_1 = coin.quote["USD"].percent_change_1h
            hour_24 = coin.quote["USD"].percent_change_24h
            days_7 = coin.quote["USD"].percent_change_7d
            hour_1_emoji = "🔼" if hour_1 >= 0 else "🔽"
            hour_24_emoji = "🔼" if hour_24 >= 0 else "🔽"
            days_7_emoji = "🔼" if days_7 >= 0 else "🔽"

            available_supply = f"{coin.circulating_supply:,.2f}"
            try:
                max_supply = f"{coin.max_supply:,.2f}"
            except (KeyError, TypeError):
                max_supply = "\N{INFINITY}"
            total_supply = f"{coin.total_supply:,.2f}"
            embed.set_thumbnail(url=coin_image)
            embed.add_field(name="Market Cap", value=f"{market_cap:,.2f} {currency}")
            embed.add_field(name="24 Hour Volume", value=f"{volume_24h:,.2f} {currency}")
            embed.add_field(name="Available Supply", value=available_supply)
            if max_supply is not None:
                embed.add_field(name="Max Supply", value=max_supply)
            embed.add_field(name="Total Supply ", value=total_supply)
            embed.add_field(name=f"Change 1 hour {hour_1_emoji}", value=f"{hour_1}%")
            embed.add_field(name=f"Change 24 hours {hour_24_emoji}", value=f"{hour_24}%")
            embed.add_field(name=f"Change 7 days {days_7_emoji}", value=f"{days_7}%")
            msg += f"Market Cap: **{market_cap}**\n24 Hour Volume: **{volume_24h}**\nAvailable Supply: **{available_supply}**\nMax Supply: **{max_supply}**\nTotal Supply: **{total_supply}**\nChange 1 hour{hour_1_emoji}: **{hour_1}%**\nChange 24 hours{hour_24_emoji}: **{hour_24}%**\nChange 7 days{days_7_emoji}: **{days_7}%**\n"

        return {"embed": embed, "content": msg}

    @commands.command(aliases=["ticker"])
    async def stock(self, ctx: commands.Context, ticker: str, currency: str = "USD") -> None:
        """Gets current ticker symbol price.

        `<ticker>` is the ticker symbol you want to look up `[currency]`
        is the currency you want to convert to defaults to USD

        """
        stock = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
        async with self.session.get(stock.format(ticker.upper())) as resp:
            data = await resp.json()
        if not data["chart"]["result"]:
            await ctx.send(f"`{ticker}` does not appear to be a valid ticker symbol.")
            return
        ticker_data = data["chart"]["result"][0]["meta"]
        if not ticker_data["currency"]:
            await ctx.send(f"`{ticker}` does not have a valid currency to view.")
            return
        convertrate: float = 1.0
        if ticker_data["currency"] != currency:
            maybe_convert = await self.conversionrate(ticker_data["currency"], currency.upper())
            if maybe_convert:
                convertrate = maybe_convert

        price = (ticker_data["regularMarketPrice"]) * convertrate
        last_updated = datetime.datetime.utcfromtimestamp(ticker_data["regularMarketTime"])
        msg = f"{ticker.upper()} is {price:,.2f} {currency.upper()}"
        embed = discord.Embed(description="Stock Price", colour=discord.Colour.lighter_grey(), timestamp=last_updated)
        embed.set_footer(text="Last Updated")
        embed.add_field(name=ticker.upper(), value=msg)
        if not ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send(msg)
        else:
            await ctx.send(embed=embed)

    @commands.command(hidden=True)
    @commands.is_owner()
    async def cryptoapi(self, ctx: commands.Context) -> None:
        """Instructions for how to setup the stock API."""
        msg = f"1. Go to https://coinmarketcap.com/api/ sign up for an account.\n2. In Dashboard / Overview grab your API Key and enter it with:\n`{ctx.prefix}set api coinmarketcap api_key YOUR_KEY_HERE`"
        await ctx.maybe_send_embed(msg)

    @commands.command(aliases=["currency"])
    async def convertcurrency(self, ctx: commands.Context, currency1: str, currency2: str, amount: float = 1.0) -> None:
        """Converts a value between 2 different currencies.

        `<currency1>` The first currency in [ISO 4217 format.](
        https://en.wikipedia.org/wiki/ISO_4217)
         `        <currency2>`The second currency in [ISO 4217 format.](
        https://en.wikipedia.org/wiki/ISO_4217)
        `[amount=1.0]`
        is the ammount you want to convert default is 1.0

        """
        currency1 = currency1.upper()
        currency2 = currency2.upper()
        if len(currency1) < 3 or len(currency1) > 3:
            await ctx.maybe_send_embed(f"{currency1} does not look like a [3 character ISO 4217 code](https://en.wikipedia.org/wiki/ISO_4217)")
            return
        if len(currency2) < 3 or len(currency2) > 3:
            await ctx.maybe_send_embed(f"{currency2} does not look like a [3 character ISO 4217 code](https://en.wikipedia.org/wiki/ISO_4217)")
            return
        conversion = await self.conversionrate(currency1, currency2)
        if conversion is None:
            await ctx.maybe_send_embed("The currencies provided are not valid!")
            return
        cost = conversion * amount
        await ctx.maybe_send_embed(f"{amount} {currency1} is {cost:,.2f} {currency2}")

    @log.catch
    async def conversionrate(self, currency1: str, currency2: str) -> Optional[float]:
        """Function to convert different currencies."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{currency1}{currency2}=x"
        async with self.session.get(url) as resp:
            data = await resp.json()
        results = data.get("chart", {}).get("result", [])
        return results[0].get("meta", {}).get("regularMarketPrice")
