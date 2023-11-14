from __future__ import annotations

import contextlib
import time

import discord
import regex as re
from aiomisc import cancel_tasks
from melaniebot.core import Config, commands
from melaniebot.core.bot import Melanie
from regex.regex import Pattern

from melanie import aiter, fmtseconds, make_e
from melanie.core import spawn_task
from melanie.helpers import get_image_colors2
from melanie.redis import get_redis

IMAGE_LINKS: Pattern[str] = re.compile(r"(http[s]?:\/\/[^\"\']*\.(?:png|jpg|jpeg|gif|png))")


class Away(commands.Cog):
    """Let your friends know when you're AFK.

    Melanie will add an autoresponder and update your nick in all shared
    servers.

    """

    default_guild_settings = {"TEXT_ONLY": False, "BLACKLISTED_MEMBERS": []}
    default_user_settings = {"MESSAGE": None, "AWAYTIME": None}

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 8_423_491_260, force_registration=True)
        self.config.register_guild(**self.default_guild_settings)
        self.config.register_user(**self.default_user_settings)
        self.cache = {}

        self.active_tasks = []
        spawn_task(self.init(), self.active_tasks)

    def cog_unload(self):
        cancel_tasks(self.active_tasks)

    async def init(self):
        all_users = await self.config.all_users()
        for uid, data in all_users.items():
            self.cache[uid] = data

    def clean_user(self, user):
        with contextlib.suppress(KeyError):
            del self.cache[user.id]

    async def get_user(self, user):
        if user not in self.cache:
            self.cache[user.id] = await self.config.user(user).all()
        return self.cache[user.id]

    async def make_embed_message(self, author: discord.User, state=None):
        """Makes the embed reply."""
        avatar = author.avatar_url_as()
        conf = await self.get_user(author)
        awaytime = conf["AWAYTIME"]
        away_message = conf["MESSAGE"]
        if away_message and (link := IMAGE_LINKS.search(away_message)):
            away_message = away_message.replace(link.group(0), " ")
        if state == "away":
            em = discord.Embed(description=away_message)
            lookup = await get_image_colors2(str(author.avatar_url))
            if lookup:
                em.color = lookup.dominant.decimal
            em.set_author(name=f"{author.display_name} is currently away".replace("(afk) ", ""), icon_url=avatar)
        em.set_footer(text=f"they've been afk for {fmtseconds(int(time.time() - awaytime),unit='seconds')}.")
        return em

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message) -> None:
        # To return from AFK listen
        if not message.guild:
            return
        if message.author.bot:
            return
        author = message.author
        conf = await self.get_user(author)
        set_afk_ts = conf.get("AWAYTIME")
        away_message = conf.get("MESSAGE")
        if away_message is None or away_message is False:
            return
        if not set_afk_ts:
            return
        if time.time() - set_afk_ts < 2:
            return

        msg = make_e(
            f"Welcome back {author.mention} <a:catkiss:1014994202649690193>",
            status="love",
            tip=f"You were away for {fmtseconds(int(time.time() - set_afk_ts),unit='seconds')}",
        )

        with contextlib.suppress(discord.HTTPException):
            try:
                await message.reply(embed=msg, mention_author=False)
            except discord.errors.HTTPException:
                await message.channel.send(embed=msg)
        await self.config.user(author).MESSAGE.set(None)
        await self.config.user(author).AWAYTIME.set(None)

        self.clean_user(author)
        m: discord.Member
        async for guild in aiter(message.author.mutual_guilds):
            if (m := guild.get_member(author.id)) and guild.me.top_role > m.top_role and guild.me.guild_permissions.administrator and guild.owner_id != m.id:
                afk_nick = m.nick
                if afk_nick and "(afk)" in afk_nick:
                    return_nick = afk_nick.replace("(afk) ", "")
                    if return_nick == m.display_name:
                        return_nick = None
                    self.bot.ioloop.add_callback(m.edit, nick=return_nick)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:  # When user is mentioned
        if not message.guild:
            return
        if not message.mentions:
            return
        if message.author.bot:
            return
        async for author in aiter(message.mentions):
            conf = await self.get_user(author)
            duration = conf.get("AWAYTIME")
            away_msg = conf.get("MESSAGE")
            if duration is not None and away_msg is not None and away_msg is not False and duration is not False:
                if time.time() - duration < 3:
                    return
                redis = get_redis()
                rl_key = f"afkpings:{message.channel.id}{message.author.id}"
                if not await redis.ratelimited(rl_key, 2, 45):
                    em = await self.make_embed_message(author, "away")
                    await message.channel.send(embed=em, delete_after=45)

    @commands.command(name="afk", aliases=["away"])
    async def away_(self, ctx: commands.Context, *, message: str = None) -> None:
        """Tell the bot you're away or back.

        `delete_after` Optional seconds to delete the automatic reply
        `message` The custom message to display when you're mentioned

        """
        author: discord.Member = ctx.message.author
        async with self.bot.redis.get_lock(f"awaylock:{ctx.author.id}"):
            async with ctx.typing():
                if await self.config.user(author).MESSAGE():
                    return

                if message is None:
                    await self.config.user(author).MESSAGE.set(" ")
                else:
                    await self.config.user(author).MESSAGE.set(message)

                await self.config.user(author).AWAYTIME.set(time.time())
                self.clean_user(author)

            try:
                await ctx.reply(embed=make_e("You're now set as away."), mention_author=False)
            except discord.errors.HTTPException:
                await ctx.channel.send(embed=make_e("You're now set as away."))
            async for guild in aiter(ctx.author.mutual_guilds):
                if m := guild.get_member(author.id):
                    m: discord.Member
                    me: discord.Member = m.guild.me
                    guild: discord.Guild = m.guild
                    if me.top_role > m.top_role and me.guild_permissions.administrator and guild.owner_id != m.id:
                        current_nickname = m.display_name
                        new_nick = f"(afk) {m.name}" if current_nickname is None else f"(afk) {current_nickname}"
                        self.bot.ioloop.add_callback(m.edit, nick=new_nick)
