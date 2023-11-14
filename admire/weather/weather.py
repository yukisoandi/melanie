from __future__ import annotations

import contextlib
from typing import Optional
from urllib.parse import urlencode

import discord
import orjson
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import Config, checks, commands


def _(x):
    return x


class UnitConverter(Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> Optional[str]:
        new_units = None
        if argument.lower() in {"f", "imperial", "mph"}:
            new_units = "imperial"
        elif argument.lower() in {"c", "metric", "kph"}:
            new_units = "metric"
        elif argument.lower() in {"k", "kelvin"}:
            new_units = "kelvin"
        elif argument.lower() in {"clear", "none"}:
            new_units = None
        else:
            msg = f"`{argument}` is not a vaild option!"
            raise BadArgument(msg)
        return new_units


class Weather(commands.Cog):
    """Get weather data from https://openweathermap.org."""

    __version__ = "1.3.0"

    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 138475464)
        default = {"units": None}
        self.config.register_global(**default)
        self.config.register_guild(**default)
        self.config.register_user(**default)
        self.unit = {
            "imperial": {"code": ["i", "f"], "speed": "mph", "temp": " °F"},
            "metric": {"code": ["m", "c"], "speed": "km/h", "temp": " °C"},
            "kelvin": {"code": ["k", "s"], "speed": "km/h", "temp": " K"},
        }

    @commands.group(name="weather", aliases=["we"], invoke_without_command=True)
    async def weather(self, ctx: commands.Context, *, location: str) -> None:
        """Display weather in a given location.

        `location` must take the form of `city, Country Code`
        example: `;weather New York,US`

        """
        async with ctx.typing():
            await self.get_weather(ctx, location=location)

    @weather.command(name="zip")
    async def weather_by_zip(self, ctx: commands.Context, *, zipcode: str) -> None:
        """Display weather in a given location.

        `zipcode` must be a valid ZIP code or `ZIP code, Country Code` (assumes US otherwise)
        example: `;weather zip 20500`

        """
        async with ctx.typing():
            await self.get_weather(ctx, zipcode=zipcode)

    @weather.command(name="cityid")
    async def weather_by_cityid(self, ctx: commands.Context, *, cityid: int) -> None:
        """Display weather in a given location.

        `cityid` must be a valid openweathermap city ID
        (get list here: <https://bulk.openweathermap.org/sample/city.list.json.gz>)
        example: `;weather cityid 2172797`

        """
        async with ctx.typing():
            await self.get_weather(ctx, cityid=cityid)

    @weather.command(name="co", aliases=["coords", "coordinates"])
    async def weather_by_coordinates(self, ctx: commands.Context, lat: float, lon: float) -> None:
        """Display weather in a given location.

        `lat` and `lon` specify a precise point on Earth using the
        geographic coordinates specified by latitude (north-south) and longitude (east-west).
        example: `;weather coordinates 35 139`

        """
        async with ctx.typing():
            await self.get_weather(ctx, lat=lat, lon=lon)

    @commands.group(name="weatherset")
    async def weather_set(self, ctx: commands.Context) -> None:
        """Set user or guild default units."""

    @weather_set.command(name="guild", aliases=["server"])
    @checks.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def set_guild(self, ctx: commands.Context, units: UnitConverter) -> None:
        """Sets the guild default weather units.

        `units` must be one of imperial, metric, or kelvin

        """
        guild = ctx.message.guild
        await self.config.guild(guild).units.set(units)
        await ctx.send(f"Server's default units set to `{str(units)}`")

    @weather_set.command(name="bot")
    @checks.mod_or_permissions(manage_messages=True)
    async def set_bot(self, ctx: commands.Context, units: UnitConverter) -> None:
        """Sets the bots default weather units.

        `units` must be one of imperial, metric, or kelvin

        """
        await self.config.units.set(units)
        await ctx.send(f"Bots default units set to {str(units)}")

    @weather_set.command(name="user")
    async def set_user(self, ctx: commands.Context, units: UnitConverter) -> None:
        """Sets the user default weather units.

        `units` must be one of imperial, metric, or kelvin
        Note: User settings override guild settings.

        """
        author = ctx.message.author
        await self.config.user(author).units.set(units)
        await ctx.send(f"{author.display_name} default units set to `{str(units)}`")

    async def get_weather(
        self,
        ctx: commands.Context,
        *,
        location: Optional[str] = None,
        zipcode: Optional[str] = None,
        cityid: Optional[int] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> None:
        guild = ctx.message.guild
        author = ctx.message.author
        bot_units = await self.config.units()
        guild_units = await self.config.guild(guild).units() if guild else None
        user_units = await self.config.user(author).units()
        units = "imperial"
        if bot_units:
            units = bot_units
        if guild_units:
            units = guild_units
        if user_units:
            units = user_units
        params = {"appid": "88660f6af079866a3ef50f491082c386", "units": units}
        if units == "kelvin":
            params["units"] = "metric"
        if zipcode:
            params["zip"] = str(zipcode)
        elif cityid:
            params["id"] = str(cityid)
        elif lon and lat:
            params["lat"] = str(lat)
            params["lon"] = str(lon)
        else:
            params["q"] = str(location)
        url = f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"

        resp = await self.bot.htx.get(url)
        data = orjson.loads(resp.content)
        with contextlib.suppress(Exception):
            if data["message"] == "city not found":
                await ctx.send("City not found.")
                return
        currenttemp = data["main"]["temp"]
        mintemp = data["main"]["temp_min"]
        maxtemp = data["main"]["temp_max"]
        city = data["name"]
        try:
            country = data["sys"]["country"]
        except KeyError:
            country = ""
        lat, lon = data["coord"]["lat"], data["coord"]["lon"]
        condition = ", ".join(info["main"] for info in data["weather"])
        windspeed = str(data["wind"]["speed"]) + " " + self.unit[units]["speed"]
        if units == "kelvin":
            currenttemp = abs(currenttemp - 273.15)
            mintemp = abs(maxtemp - 273.15)
            maxtemp = abs(maxtemp - 273.15)
        sunrise_timestamp = int(data["sys"]["sunrise"])
        sunset_timestamp = int(data["sys"]["sunset"])
        sunrise = f"<t:{sunrise_timestamp}:t>"
        sunset = f"<t:{sunset_timestamp}:t>"
        embed = discord.Embed(colour=discord.Colour.blue())
        if len(city) and len(country):
            embed.add_field(name="🌍 **Location**", value=f"{city}, {country}")
        else:
            embed.add_field(name="\N{EARTH GLOBE AMERICAS} **Location**", value="*Unavailable*")
        embed.add_field(name="\N{STRAIGHT RULER} **Lat,Long**", value=f"{lat}, {lon}")
        embed.add_field(name="\N{CLOUD} **Condition**", value=condition)
        embed.add_field(name="\N{FACE WITH COLD SWEAT} **Humidity**", value=data["main"]["humidity"])
        embed.add_field(name="\N{DASH SYMBOL} **Wind Speed**", value=f"{windspeed}")
        embed.add_field(name="\N{THERMOMETER} **Temperature**", value=f"{currenttemp:.2f}{self.unit[units]['temp']}")
        embed.add_field(
            name="\N{HIGH BRIGHTNESS SYMBOL} **Min - Max**",
            value=f"{mintemp:.2f}{self.unit[units]['temp']} to {maxtemp:.2f}{self.unit[units]['temp']}",
        )
        embed.add_field(name="\N{SUNRISE OVER MOUNTAINS} **Sunrise**", value=sunrise)
        embed.add_field(name="\N{SUNSET OVER BUILDINGS} **Sunset**", value=sunset)
        embed.set_footer(text="Powered by https://openweathermap.org")
        await ctx.send(embed=embed)
