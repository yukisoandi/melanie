from __future__ import annotations

import asyncio
import textwrap
from contextlib import suppress

import async_cse
import discord
import requests
from cashews.exceptions import NotConfiguredError
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from image.google import SearchItem, SearchResult, fetch_web_html, process_search_markup
from melanie import aiter, checkpoint, rcache, url_concat
from melanie.core import spawn_task
from melanie.curl import SHARED_API_HEADERS, CurlError
from melanie.helpers import fetch_gif_if_tenor, footer_gif, get_image_colors2, make_e
from melanie.models.sharedapi.pinterest.reverse import PinterestReverseResult
from melanie.vendor.disputils import BotEmbedPaginator
from notsobot.converter import ImageFinder

rcache.setup(
    "redis://melanie.melaniebot.net",
    secret=None,
    enable=True,
    suppress=True,
    max_connections=100,
)


class Image(commands.Cog):
    """Useful commands for server administrators."""

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot
        self.config = Config.get_conf(self, 95932766180343808, force_registration=True)
        self.config.register_guild(safe_search=True)
        self.pin_lock = asyncio.Lock()
        self.active_tasks = []
        self.google = async_cse.Search("AIzaSyCbGNDjyhl238ze47Q3LtJ4_YnC15Ck8ZE", engine_id="a2c452bf5cf5d4f9b", image_engine_id="a2c452bf5cf5d4f9b")

    @commands.guild_only()
    @commands.cooldown(1, 25, commands.BucketType.user)
    @commands.command(aliases=["gofish"])
    async def pinsearch(self, ctx: commands.Context, image: ImageFinder = None):
        """Reverse search an image with the Pinterest Lens.

        Usually only returns results for high quality, original images.
        Try ;reverse for a regular Google images search.

        """
        if not image:
            image = await ImageFinder().search_for_images(ctx)
        url = str(image[0])
        async with (asyncio.timeout(90), ctx.typing(), self.pin_lock):
            uri = url_concat("https://dev.melaniebot.net/api/pinterest/reverse", {"img_url": url, "user_id": ctx.author.id})
            try:
                r = await self.bot.curl.fetch(uri, headers=SHARED_API_HEADERS)
            except CurlError:
                return await ctx.send(
                    embed=make_e("No results for that image. Try another image.", 3, tip="I can only search using high resolution images."),
                )

            result = PinterestReverseResult.parse_raw(r.body)
            embeds: list[discord.Embed] = []
            if not result.data:
                return await ctx.send(
                    embed=make_e("No results for that image. Try another image.", 3, tip="I can only search using high resolution images."),
                )

            async for item in aiter(result.data):
                if not item.description:
                    item.description = ""
                if not item.title:
                    item.title = ""
                title = item.description if len(item.description) > len(item.title) else item.title

                em = discord.Embed()
                em.url = f"https://www.pinterest.com/pin/{item.id}/"
                em.title = title
                em.description = f"Reverse Pinterest Search ({len(result.data)} pins)"
                if not embeds:
                    with suppress(asyncio.TimeoutError):
                        async with asyncio.timeout(2):
                            lookup = await get_image_colors2(item.image_medium_url)
                            if lookup:
                                em.color = lookup.dominant.decimal

                em.set_image(url=item.image_medium_url)
                em.set_footer(icon_url=footer_gif, text="melanie ^_^")
                embeds.append(em)

            paginator = BotEmbedPaginator(ctx, embeds)
            spawn_task(paginator.run(), self.active_tasks)

    @rcache(ttl="8d", key="googlesrc:{query}:{safe}")
    async def googlesearch(self, query, safe):
        markup = self.bot.dask.submit(fetch_web_html, query, safe, pure=True)
        task = self.bot.dask.submit(process_search_markup, markup)
        return await task

    @commands.cooldown(10, 9, commands.BucketType.guild)
    @commands.command(aliases=["image"])
    async def img(self, ctx: commands.Context, *, search: str) -> None:
        """Lookup images from Azure Cognitive Search."""
        async with (ctx.typing(), asyncio.timeout(20)):
            if not ctx.channel.is_nsfw():
                safe_setting = True
            else:
                safe_setting = await self.config.guild(ctx.guild).safe_search()
            search = " ".join(search.lower().split())

            try:
                cached = await self.googlesearch(search, safe_setting)

            except NotConfiguredError:
                cached = await self.googlesearch(search, safe_setting)

            if cached:
                r = SearchResult.parse_raw(cached)
                if not r.items:
                    cached = None

            embeds: list[discord.Embed] = []
            for i in r.items:
                i: SearchItem
                e = discord.Embed()
                e.url = i.link
                e.title = i.source
                e.description = textwrap.shorten(i.title, width=520, placeholder="..")
                e.set_footer(text="Google Image Search", icon_url="https://hurt.af/gif/google.png")
                if len(embeds) > 8:
                    e.color = discord.Colour(9936031)
                else:
                    await checkpoint()
                e.set_image(url=i.original)
                embeds.append(e)
            if not embeds:
                return await ctx.send(embed=make_e("No results found for that search..", 2))
            paginator = BotEmbedPaginator(ctx, embeds)
            spawn_task(paginator.run(), self.active_tasks)

    @commands.guild_only()
    @commands.cooldown(10, 9, commands.BucketType.guild)
    @commands.command(aliases=["tenor"])
    async def gif(self, ctx: commands.Context, message: discord.Message = None):
        """Convert a tenor URL to a direct GIF URL.

                Reply to the Tenor GIF with this command or provide a message
                link/ID of a message in this channel.
        `
        """
        async with ctx.typing(), asyncio.timeout(10):
            if not ctx.message.reference and not message:
                return await ctx.send_help()
            if ctx.message.reference and not message:
                message = ctx.message.reference.cached_message or await ctx.channel.fetch_message(ctx.message.reference.message_id)
            content = message.content
            tenor_gif = await fetch_gif_if_tenor(content=content)
            if not tenor_gif:
                return await ctx.send(embed=make_e("No Tenor GIF was found from that message", status=2))
            embed = discord.Embed(title="Tenor URL to GIF", description=tenor_gif)
            lookup = await get_image_colors2(tenor_gif)
            if lookup:
                embed.color = lookup.dominant.decimal
            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
            embed.set_image(url=tenor_gif)
            return await ctx.send(embed=embed)

    @checks.has_permissions(administrator=True)
    @commands.group()
    async def imageset(self, ctx: commands.Context) -> None:
        """Toggle settings related to Image search."""

    @imageset.command(name="safe")
    @commands.guild_only()
    @commands.cooldown(1, 9, commands.BucketType.user)
    @commands.max_concurrency(4, commands.BucketType.guild)
    async def imageset_safe(self, ctx: commands.Context):
        """Toggle on/off images safe search."""
        current_option = await self.config.guild(ctx.guild).safe_search()
        if current_option:
            await self.config.guild(ctx.guild).safe_search.set(False)
            return await ctx.send("Safe search disabled.")
        await self.config.guild(ctx.guild).safe_search.set(True)
        return await ctx.send("Enabled safe search.")

    # Below are all the blocking code


def run_search_query(query: str, safe_mode: bool = True):
    # run search, get results, send back to bot pickle.

    safe = "Moderate" if safe_mode else "Off"
    subscription_key = "96f93cad7b0b4bf7a9e045a8ee45c6bf"
    search_url = "https://api.bing.microsoft.com/v7.0/images/search"
    search_term = query
    headers = {"Ocp-Apim-Subscription-Key": subscription_key}
    params = {"q": search_term, "imageType": "photo", "safeSearch": safe, "count": 50}
    response = requests.get(search_url, headers=headers, params=params)
    response.raise_for_status()
    search_results = response.json()

    return [(img["thumbnailUrl"], img["name"], img["contentUrl"]) for img in search_results["value"]]
