from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter, deque
from itertools import chain, repeat

import discord
import orjson
from async_lru import alru_cache
from boltons.iterutils import chunked
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie

from melanie import cancel_tasks, get_curl, get_redis


def _(x):
    return x


WORDS_URL = "https://static.hurt.af/words_dictionary.json"
GAMES_KEY = "pg_words"


def windowed(seq, n, fillvalue=None, step=1):
    if n < 0:
        msg = "n must be >= 0"
        raise ValueError(msg)
    if n == 0:
        yield ()
        return
    if step < 1:
        msg = "step must be >= 1"
        raise ValueError(msg)

    window = deque(maxlen=n)
    i = n
    for _ in map(window.append, seq):
        i -= 1
        if not i:
            i = step
            yield tuple(window)

    size = len(window)
    if size == 0:
        return
    elif size < n:
        yield tuple(chain(window, repeat(fillvalue, n - size)))
    elif 0 < i < min(step, n):
        window += (fillvalue,) * i
        yield tuple(window)


async def get_all_words():
    curl = get_curl()
    r = await curl.fetch(WORDS_URL)
    return orjson.loads(r.body)


async def build_words_set():
    redis = get_redis()
    if await redis.sismember("english_words", "dog"):
        return True

    _words = await get_all_words()
    words = list(_words.keys())

    async with redis.pipeline() as pipe:
        pipe.delete("english_words")
        for w in words:
            pipe.sadd("english_words", w.lower())
        await pipe.execute()


async def build_token_set():
    redis = get_redis()

    curl = get_curl()
    counter = Counter()
    added_tokens = []
    all_words = await get_all_words()
    url = "https://hurt.af/static/google-10000-english.txt"
    url = "https://raw.githubusercontent.com/dolph/dictionary/master/popular.txt"

    r = await curl.fetch(url)
    words = sorted(r.body.decode().splitlines())
    for word in words:
        if len(word) > 3:
            for chunk in chunked(word, 3):
                if len(chunk) == 3:
                    if all_words.get(chunk) is None:
                        counter[chunk] = 1
                    break

    async with redis.pipeline() as pipe:
        pipe.delete("english_tokens")
        for v in counter.most_common(150):
            pipe.sadd("english_tokens", v[0])
            added_tokens.append(v[0])

        await pipe.execute()
    return counter


class PartyGames(commands.Cog):
    """Chat games focused on coming up with words from 3 letters."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=145519400223506432)
        self.config.register_guild(locale=None, timeBomb=7, timeFast=15, timeLong=15, timeMost=15)
        self.waiting = {}
        self.games = []
        self.update_lock = asyncio.Lock()
        self.closed = False
        self.active_tasks = []

    @alru_cache
    async def get_english_wordlist(self):
        words = await get_all_words()
        return list(words.keys())

    def cog_unload(self) -> None:
        self.closed = True
        cancel_tasks(self.active_tasks)

    @property
    def butterfly_emote(self):
        return self.bot.get_emoji(1014994185520169041) or "🦋"

    @commands.group(aliases=["pg"])
    async def partygames(self, ctx: commands.Context) -> None:
        """Group command for party games."""

    async def _get_players(self, ctx):
        """Helper function to set up a game."""
        msg = await ctx.send("The game will start in 15 seconds!\n\n**Game is open to all in chat.**")
        await msg.add_reaction(self.butterfly_emote)
        max_wait = time.time() + 15
        while time.time() < max_wait:
            if self.closed:
                with contextlib.suppress(discord.NotFound):
                    await msg.delete()
                return await ctx.send("Exited.")
            await asyncio.sleep(0.5)

        # edge case test for the reaction being removed from the message
        return ctx.guild.members

    async def _get_wordlist(self, ctx: commands.Context):
        """Get the proper wordlist for the current locale."""
        async with asyncio.timeout(20):
            wordlist = await self.get_english_wordlist()
            return (wordlist, "en-US")

    @staticmethod
    def _get_name_string(ctx, uid: int, domention: bool):
        """Returns a member identification string from an id, checking for
        exceptions.
        """
        if member := ctx.guild.get_member(uid):
            return member.mention if domention else member.display_name
        return f"<removed member {uid}>"

    def _make_leaderboard(self, ctx, scores) -> str:
        """Returns a printable version of the dictionary."""
        order = sorted(scores, key=lambda m: scores[m], reverse=True)
        msg = "Number of points:\n"
        for uid in order:
            if not scores[uid]:
                continue
            name = self._get_name_string(ctx, uid, False)
            msg += f"{scores[uid]} {name}\n"
        return f"```{msg}```"

    @partygames.command()
    async def fast(self, ctx: commands.Context, maxpoints: int = 5) -> None:
        """Race to type a word the fastest.

        The first person to type a word that contains the given
        characters gets a point. Words cannot be reused. The first
        person to get `maxpoints` points wins.

        """
        task = asyncio.current_task()
        self.active_tasks.append(task)
        task.add_done_callback(self.active_tasks.remove)

        async with ctx.typing():
            await build_token_set()
        if ctx.channel.id in self.games:
            await ctx.send("There is already a game running in this channel!")
            return
        self.games.append(ctx.channel.id)
        players = await self._get_players(ctx)
        if self.closed:
            return
        if ctx.author.id not in self.bot.owner_ids and len(players) <= 1:
            await ctx.send("Not enough players to play.")
            if ctx.channel.id in self.games:
                self.games.remove(ctx.channel.id)
            return
        (wordlist, locale) = await self._get_wordlist(ctx)
        score = {p.id: 0 for p in players}
        game = True
        used = []
        afk = 0

        try:
            while game and not self.closed:
                (score, used, mem) = await self._fast(ctx, score, used, players, wordlist, locale)
                if mem is None:
                    afk += 1
                    if afk == 3:
                        await ctx.send(f"No one wants to play :(\n{self._make_leaderboard(ctx, score)}")
                        game = False
                    else:
                        await ctx.send("No one was able to come up with a word!")
                else:
                    afk = 0
                    if score[mem.id] >= maxpoints:
                        await ctx.send(f"{mem.mention} wins!\n{self._make_leaderboard(ctx, score)}")
                        game = False
                await asyncio.sleep(3)
        except asyncio.CancelledError as e:
            self.bot.ioloop.add_callback(ctx.send, "Game force ended by an admin.")
            raise e

        if ctx.channel.id in self.games:
            self.games.remove(ctx.channel.id)

    async def _fast(self, ctx, score, used, players, wordlist, locale):
        c = await self.get_random_char()
        await ctx.send(f"Be the first person to type a word containing: **{c}**")
        try:
            word = await self.bot.wait_for(
                "message",
                timeout=await self.config.guild(ctx.guild).timeFast(),
                check=lambda m: m.channel == ctx.channel
                and m.author.id in score
                and c.lower() in m.content.lower()
                and m.content.lower() in wordlist
                and m.content.lower() not in used,
            )

        except TimeoutError:
            return (score, used, None)
        else:
            await word.add_reaction(self.butterfly_emote)
            score[word.author.id] += 1
            await ctx.send(f"{word.author.mention} gets a point! ({score[word.author.id]} total)")
            used.append(word.content.lower())
            return (score, used, word.author)

    async def get_random_char(self):
        token: bytes = await self.bot.redis.spop("english_tokens")
        return token.decode("UTF-8").upper()

    # @partygames.command()
    # async def long(self, ctx, maxpoints: int = 5) -> None:
    #     """
    #     Type the longest word.

    #     The person to type the longest word that contains the given
    #     characters gets a point. Words cannot be reused. The first
    #     person to get `maxpoints` points wins.

    #     """
    #     if ctx.channel.id in self.games:
    #     if self.closed:
    #     if len(players) <= 1:
    #         if ctx.channel.id in self.games:
    #     while game and not self.closed:
    #         if mem is None:
    #             if afk == 3:
    #             if score[mem.id] >= maxpoints:
    #     if ctx.channel.id in self.games:

    async def _long(self, ctx, score, used, players, wordlist, locale):
        c = await self.get_random_char()

        timeLong = await self.config.guild(ctx.guild).timeLong()
        await ctx.send(f"Type the longest word containing: **{c}**")
        self.waiting[ctx.channel.id] = {
            "type": "long",
            "plist": [p.id for p in players],
            "chars": c,
            "used": used,
            "best": "",
            "bestmem": None,
            "wordlist": wordlist,
        }
        await asyncio.sleep(timeLong)
        resultdict = self.waiting[ctx.channel.id]
        del self.waiting[ctx.channel.id]
        if resultdict["best"] == "":
            return (score, used, None)
        score[resultdict["bestmem"].id] += 1
        await ctx.send(f"{resultdict['bestmem'].mention} gets a point! ({score[resultdict['bestmem'].id]} total)")
        used.append(resultdict["best"].lower())
        return (score, used, resultdict["bestmem"])

    # @partygames.command()
    # async def most(self, ctx, maxpoints: int = 5) -> None:
    #     """
    #     Type the most words.

    #     The person to type the most words that contain the given
    #     characters gets a point. Words cannot be reused. The first
    #     person to get `maxpoints` points wins.

    #     """
    #     if ctx.channel.id in self.games:
    #     if self.closed:
    #     if len(players) <= 1:
    #         if ctx.channel.id in self.games:
    #     while game and not self.closed:
    #         if mem is None:
    #             if afk == 3:
    #             if score[mem.id] >= maxpoints:
    #     if ctx.channel.id in self.games:

    async def _most(self, ctx, score, used, players, wordlist, locale):
        c = await self.get_random_char()
        await ctx.send(f"Type the most words containing: **{c}**")
        timeMost = await self.config.guild(ctx.guild).timeMost()
        self.waiting[ctx.channel.id] = {"type": "most", "pdict": {p.id: [] for p in players}, "chars": c, "used": used, "wordlist": wordlist}
        await asyncio.sleep(timeMost)
        resultdict = self.waiting[ctx.channel.id]
        del self.waiting[ctx.channel.id]
        used = resultdict["used"]
        order = sorted(resultdict["pdict"], key=lambda m: len(resultdict["pdict"][m]), reverse=True)
        if resultdict["pdict"][order[0]] == []:
            return (score, used, None)
        winners = []
        for uid in order:
            if len(resultdict["pdict"][uid]) == len(resultdict["pdict"][order[0]]):
                winners.append(uid)
            else:
                break
        msg = "Number of words found:\n"
        for uid in order:
            name = self._get_name_string(ctx, uid, False)
            msg += f'{len(resultdict["pdict"][uid])} {name}\n'
        await ctx.send(f"```{msg}```")

        if len(winners) == 1:
            score[order[0]] += 1
            await ctx.send(f"{self._get_name_string(ctx, order[0], True)} gets a point! ({score[order[0]]} total)")
            return (score, used, ctx.guild.get_member(order[0]))
            # in the very specific case of a member leaving after becoming the person
            # with the most words, this will return None and do a weird double print.
            # deal with it.
        return (score, used, False)  # tie

    # @partygames.command()
    # async def mix(self, ctx, maxpoints: int = 5) -> None:
    #     """
    #     Play a mixture of all 4 games.

    #     Words cannot be reused. The first person to get `maxpoints`
    #     points wins.

    #     """
    #     if ctx.channel.id in self.games:
    #     if self.closed:
    #     if len(players) <= 1:
    #         if ctx.channel.id in self.games:
    #     while game and not self.closed:
    #         if g == 3:
    #             for p in players:
    #                         "message",
    #                         and m.author.id == p.id
    #                         and c.lower() in m.content.lower()
    #                         and m.content.lower() in wordlist
    #                         and m.content.lower() not in used,

    #                     if score[p.id] >= maxpoints:
    #             if mem is None:
    #                 if afk == 3:
    #                 if score[mem.id] >= maxpoints:
    #     if ctx.channel.id in self.games:

    @checks.guildowner()
    @commands.group(aliases=["pgset"])
    async def partygamesset(self, ctx) -> None:
        """Config options for partygames."""

    @partygamesset.command()
    async def fasttime(self, ctx, value: int = None):
        """Set the timeout of fast.

        Defaults to 15. This value is server specific.

        """
        if value is None:
            v = await self.config.guild(ctx.guild).timeFast()
            await ctx.send(f"The timeout is currently set to {v}.")
        else:
            if value <= 0:
                return await ctx.send("That value is too low.")
            await self.config.guild(ctx.guild).timeFast.set(value)
            await ctx.send(f"The timeout is now set to {value}.")

    @partygamesset.command()
    async def longtime(self, ctx, value: int = None):
        """Set the timeout of long.

        Defaults to 15. This value is server specific.

        """
        if value is None:
            v = await self.config.guild(ctx.guild).timeLong()
            await ctx.send(f"The timeout is currently set to {v}.")
        else:
            if value <= 0:
                return await ctx.send("That value is too low.")
            await self.config.guild(ctx.guild).timeLong.set(value)
            await ctx.send(f"The timeout is now set to {value}.")

    @partygamesset.command()
    async def mosttime(self, ctx, value: int = None):
        """Set the timeout of most.

        Defaults to 15. This value is server specific.

        """
        if value is None:
            v = await self.config.guild(ctx.guild).timeMost()
            await ctx.send(f"The timeout is currently set to {v}.")
        else:
            if value <= 0:
                return await ctx.send("That value is too low.")
            await self.config.guild(ctx.guild).timeMost.set(value)
            await ctx.send(f"The timeout is now set to {value}.")

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        if not self.bot.is_ready():
            return
        # This func cannot use cog_disabled_in_guild, or the game will continute to running
        # and send messages w/o any way to stop it.
        if message.author.bot:
            return
        if message.guild is None:
            return
        if message.channel.id in self.waiting:
            if self.waiting[message.channel.id]["type"] == "long":
                if (
                    message.author.id in self.waiting[message.channel.id]["plist"]
                    and self.waiting[message.channel.id]["chars"].lower() in message.content.lower()
                    and message.content.lower() in self.waiting[message.channel.id]["wordlist"]
                    and message.content.lower() not in self.waiting[message.channel.id]["used"]
                    and len(message.content) > len(self.waiting[message.channel.id]["best"])
                ):
                    self.waiting[message.channel.id]["best"] = message.content.lower()
                    self.waiting[message.channel.id]["bestmem"] = message.author
                    await message.add_reaction(self.butterfly_emote)
            elif self.waiting[message.channel.id]["type"] == "most" and (
                message.author.id in self.waiting[message.channel.id]["pdict"]
                and self.waiting[message.channel.id]["chars"].lower() in message.content.lower()
                and message.content.lower() in self.waiting[message.channel.id]["wordlist"]
                and message.content.lower() not in self.waiting[message.channel.id]["used"]
            ):
                self.waiting[message.channel.id]["used"].append(message.content.lower())
                self.waiting[message.channel.id]["pdict"][message.author.id].append(message.content.lower())
                await message.add_reaction(self.butterfly_emote)
