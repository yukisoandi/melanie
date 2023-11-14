import asyncio
import os
from collections import defaultdict
from functools import partial
from typing import Optional, Union

import discord
import orjson
from aiobotocore.session import get_session
from aiomisc.utils import cancel_tasks
from anyio import Path as AsyncPath
from melaniebot.core import commands
from melaniebot.core.bot import Melanie
from melaniebot.core.data_manager import cog_data_path

from melanie import (
    alru_cache,
    checkpoint,
    footer_gif,
    get_curl,
    make_e,
    normalize_smartquotes,
)
from melanie.curl import get_curl

from .models import ChatResponse

default_sem = partial(asyncio.Semaphore, 3)


class GPTClient:
    def __init__(self) -> None:
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.getenv("OPENAI_API_KEY", "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"),
        }
        self.curl = get_curl()
        self.active_tasks: list[asyncio.Task] = []

    def close(self):
        cancel_tasks(self.active_tasks)

    @alru_cache(maxsize=500)
    async def chat_request(self, message: str) -> ChatResponse:
        data = {"model": "gpt-4", "messages": [{"role": "user", "content": message}]}

        r = await self.curl.fetch("https://api.openai.com/v1/chat/completions", headers=self.headers, body=orjson.dumps(data), method="POST")
        return ChatResponse.parse_raw(r.body)


class ChatGPT(commands.Cog):
    """Store images as commands!."""

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot

        self.session = get_session()
        self.root = AsyncPath(str(cog_data_path(self).absolute()))
        self.gpt = GPTClient()
        self.guild_sems: dict[int, asyncio.Semaphore] = defaultdict(default_sem)
        self.active_tasks: list[asyncio.Task] = []

    def bind_task(self):
        task = asyncio.current_task()
        self.active_tasks.append(task)
        task.add_done_callback(self.active_tasks.remove)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def is_ratelimited(self, user: discord.User) -> bool:
        if user.id in self.bot.owner_ids:
            return False
        ratelimit = 50 if await self.bot.redis.sismember("paid_plus_users", user.id) else 3

        key = f"chatgpt:{user.id}"

        return await self.bot.redis.ratelimited(key, ratelimit - 1, 86400)

    async def ask_api(self, ctx: commands.Context, question: str):
        self.bind_task()
        sem = self.guild_sems[ctx.guild.id]
        if sem.locked():
            return await ctx.message.add_reaction("🔐")
        async with sem:
            await checkpoint()
            async with ctx.typing():
                if await self.is_ratelimited(ctx.author):
                    return await ctx.send(
                        embed=make_e(
                            "You've reached your limit on chat requests for the day. Requests are highly ratelimited for non-supporters. Join https://discord.gg/melaniebot and donate or obtain melanie paid+ to have higher ratelimits.",
                            2,
                        ),
                    )

                try:
                    async with asyncio.timeout(60):
                        result = await self.gpt.chat_request(question)
                        text = result.choices[0].message.content
                        text = text.replace("As an AI language model, ", "")
                        if "OpenAI" in text:
                            return await ctx.message.add_reaction("🔐")
                        embed = discord.Embed()
                        embed.set_author(name=ctx.author.display_name, icon_url=str(ctx.author.avatar_url))
                        embed.description = text
                        embed.set_footer(text="melanie", icon_url=footer_gif)

                        try:
                            if len(text) < 1000:
                                return await ctx.reply(content=text)
                            return await ctx.reply(embed=embed)
                        except discord.HTTPException:
                            if len(text) < 1000:
                                return await ctx.reply(content=text)
                            return await ctx.send(embed=embed)

                except TimeoutError:
                    return await ctx.send(embed=make_e("Timedout generating a response for that question.."))

    @commands.command(aliases=["summerize"])
    async def tldr(self, ctx: commands.Context, *, message: Optional[Union[discord.Message, str]]):
        if not message and not ctx.message.reference:
            return await ctx.send_help()
        if not message:
            message = ctx.message.reference.cached_message or await ctx.channel.fetch_message(ctx.message.reference.message_id)
        question = normalize_smartquotes(message.content) if isinstance(message, discord.Message) else normalize_smartquotes(message)

        question = f"Summerize this text in less than 190 characters: {question}"

        return await self.ask_api(ctx, question)

    @commands.command()
    async def ask(self, ctx: commands.Context, *, question: str):
        question = normalize_smartquotes(question)

        return await self.ask_api(ctx, question)
