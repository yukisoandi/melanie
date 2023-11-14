import asyncio

import discord
from melaniebot.core import Config, commands

from melanie import SHARED_API_HEADERS, get_curl, get_image_colors2, intword
from melanie.helpers import make_e
from melanie.models.sharedapi.twitter.userinfo import TwitterUserDataRaw


def _(x):
    return x


class Twitter(commands.Cog):
    """Cog for displaying info from Twitter's API."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 133926854, force_registration=True)
        default_global = {
            "api": {"consumer_key": "", "consumer_secret": "", "access_token": "", "access_secret": ""},
            "accounts": {},
            "error_channel": None,
            "error_guild": None,
            "schema_version": 0,
        }
        self.config.register_global(**default_global)
        self.config.register_channel(custom_embeds=True)
        self.mystream = None
        self.run_stream = True
        self.twitter_loop = None

    @commands.group(name="twitter", invoke_without_command=True, aliases=["x"])
    async def _twitter(self, ctx: commands.Context, *, username: str = None):
        """Gets various information from Twitter's API."""
        return await self.get_user(ctx, username) if username else await ctx.send_help()

    async def get_user(self, ctx: commands.context, username: str) -> None:
        """Get info about the specified user."""
        curl = get_curl()
        lookup = None
        async with ctx.typing():
            async with asyncio.timeout(15):
                url = f"https://dev.melaniebot.net/api/twitter/{username}"
                r = await curl.fetch(url, headers=SHARED_API_HEADERS)
                data = TwitterUserDataRaw.parse_raw(r.body)
            if data.suspended:
                return await ctx.send(embed=make_e(f"User **{username}** is suspended!", 2))

            profile_url = f"https://twitter.com/{username}"
            description = str(data.info.legacy.description)

            if data.info.legacy.entities and data.info.legacy.entities.url:
                for url in data.info.legacy.entities.url.urls:
                    if url.url in description:
                        description = description.replace(url.url, url.expanded_url)
            embed = discord.Embed(url=profile_url, description=str(description))

            embed.title = f"{data.info.legacy.name} (@{username})"
            if data.info.verified:
                embed.title = f"{embed.title} ⭐️"
            if data.info.legacy.profile_banner_url:
                lookup = await get_image_colors2(data.info.legacy.profile_banner_url)
                if lookup:
                    embed.color = lookup.dominant.decimal
                    embed.set_image(url=data.info.legacy.profile_banner_url)

            if data.info.legacy.profile_image_url_https:
                avatar_url = data.info.legacy.profile_image_url_https.replace("normal.jpg", "400x400.jpg")

                embed.set_thumbnail(url=avatar_url)
            embed.add_field(name="tweets", value=intword(data.info.legacy.statuses_count))
            embed.add_field(name="following", value=intword(data.info.legacy.friends_count))

            embed.add_field(name="followers", value=intword(data.info.legacy.followers_count))
            embed.set_footer(text="twitter", icon_url="https://cdn.discordapp.com/attachments/928400431137296425/1071274971420184660/twitter-logo-2429.png")
            if data.info.legacy.location:
                embed.add_field(name="location", value=data.info.legacy.location)
        try:
            await ctx.reply(embed=embed)
        except discord.HTTPException:
            return await ctx.send(embed=embed)
