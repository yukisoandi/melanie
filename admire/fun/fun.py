from __future__ import annotations

import asyncio
import contextlib
import io
import itertools
import random
import socket
import textwrap
import time
import typing
from collections import defaultdict
from typing import Any, Optional, Union

import aiohttp
import arrow
import discord
import filetype
import genshin
import httpx
import humanize
import msgpack
import orjson
import regex as re
import tuuid
import xxhash
import yarl
from asyncstdlib import nullcontext
from boltons.urlutils import find_all_links
from filetype import guess_extension, guess_mime
from genshin.errors import DataNotPublic
from humanize import intcomma
from lru import LRU
from melaniebot.core import checks, commands
from melaniebot.core.config import Config
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu
from rapidfuzz.fuzz import ratio
from rapidfuzz.process import extractOne
from rapidfuzz.utils import default_process
from tornado.curl_httpclient import CurlError

from audio.core.abc import MixinMeta as AudioMeta
from executionstracker.exe import ExecutionsTracker
from melanie import (
    BaseModel,
    CurlError,
    Timeit,
    alru_cache,
    bytes2human,
    cancel_tasks,
    checkpoint,
    create_task,
    default_lock_cache,
    footer_gif,
    get_image_colors2,
    log,
    make_e,
    yesno,
)
from melanie.core import spawn_task
from melanie.curl import SHARED_API_HEADERS, CurlRequest, get_curl, url_concat
from melanie.models.sharedapi.ai import AIImageGenerationResponse
from melanie.models.sharedapi.speech import STTResult
from melanie.models.sharedapi.tts import TTSTranslationRequest
from melanie.models.sharedapi.vision import OCRReadResponse
from melanie.timing import fmtseconds
from notsobot.converter import ImageFinder, VideoFinder
from runtimeopt.disk_cache import MelanieCache
from videofetch.gif import GifRenderJobResult, convert_to_gif

from .helpers.constants import emoji_dict
from .helpers.converters import AudioVideoFindeer
from .helpers.methods import generate_bigmoji4, get_osu_user
from .helpers.text import extract_url_format, has_dupe, replace_combos, replace_letters
from .models.character import GenshinCharacter

if typing.TYPE_CHECKING:
    from re import Pattern

    from genshin.models import GenshinUserStats
    from melaniebot.core.bot import Melanie

    from antinuke.antinuke import AntiNuke

    from .models.osu import OsuUser

GENSHIN_COOKIES = {
    "account_id": "242437389",
    "cookie_token": "JmTofMvahlhlEgE7vmFfqc5ltywNfCfTTIF6nHGd",
    "ltoken": "ypMhjEuqvYtRzLSLEigAhq8UYq0vTA0krENQ5B20",
    "ltuid": "242437389",
}
INVITE_RE: Pattern = re.compile(r"(?:https?\:\/\/)?discord(?:\.gg|(?:app)?\.com\/invite)\/(.+)", re.I)

SPEAKERS = {
    "amber": {"name": "en-US-Amber", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Amber"},
    "ana": {"name": "en-US-Ana", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Ana"},
    "aria": {"name": "en-US-Aria", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Aria"},
    "ashley": {"name": "en-US-Ashley", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Ashley"},
    "brandon": {"name": "en-US-Brandon", "gender": "None", "locale": "en-US", "local_name": "Brandon"},
    "christopher": {"name": "en-US-Christopher", "gender": "None", "locale": "en-US", "local_name": "Christopher"},
    "cora": {"name": "en-US-Cora", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Cora"},
    "davis": {"name": "en-US-Davis", "gender": "None", "locale": "en-US", "local_name": "Davis"},
    "elizabeth": {"name": "en-US-Elizabeth", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Elizabeth"},
    "eric": {"name": "en-US-Eric", "gender": "None", "locale": "en-US", "local_name": "Eric"},
    "guy": {"name": "en-US-Guy", "gender": "None", "locale": "en-US", "local_name": "Guy"},
    "jacob": {"name": "en-US-Jacob", "gender": "None", "locale": "en-US", "local_name": "Jacob"},
    "jane": {"name": "en-US-Jane", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Jane"},
    "jason": {"name": "en-US-Jason", "gender": "None", "locale": "en-US", "local_name": "Jason"},
    "jenny": {"name": "en-US-Jenny", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Jenny"},
    "michelle": {"name": "en-US-Michelle", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Michelle"},
    "monica": {"name": "en-US-Monica", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Monica"},
    "nancy": {"name": "en-US-Nancy", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Nancy"},
    "sara": {"name": "en-US-Sara", "gender": "SynthesisVoiceGender.Female", "locale": "en-US", "local_name": "Sara"},
    "tony": {"name": "en-US-Tony", "gender": "None", "locale": "en-US", "local_name": "Tony"},
    "maisie": {"name": "en-GB-Maisie", "gender": "SynthesisVoiceGender.Female", "locale": "en-GB", "local_name": "Maisie"},
    "abbi": {"name": "en-GB-Abbi", "gender": "SynthesisVoiceGender.Female", "locale": "en-GB", "local_name": "Abbi"},
    "prabhat": {"name": "en-IN-Prabhat", "gender": "SynthesisVoiceGender.Female", "locale": "en-IN", "local_name": "Prabhat"},
    "tim": {"name": "en-AU-Tim", "gender": "SynthesisVoiceGender.Male", "locale": "en-AU", "local_name": "Tim"},
}


class UserSettings(BaseModel):
    osu_username: Optional[str]
    genshin_id: Optional[int]
    custom_prefix: Optional[str]
    marriage_key: Optional[str]
    genshin_user_history: Optional[dict[Any, Any]] = {}

    @property
    def last_genshin_profile(self) -> GenshinUserStats:
        latest_key = max(self.genshin_user_history.keys())
        return self.genshin_user_history[latest_key]


def get_case_values(chars: str) -> tuple:
    return tuple(map("".join, itertools.product(*zip(chars.upper(), chars.lower()))))


async def can_name_resolve(
    host: str,
    port: int = 80,
) -> Union[list[tuple[socket.AddressFamily, socket.SocketKind, int, str, Union[tuple[str, int], tuple[str, int, int, int]]]], bool]:
    async with asyncio.timeout(5):
        try:
            return await asyncio.get_event_loop().getaddrinfo(host, port)
        except socket.gaierror:
            return False


class MarriageSettings(BaseModel):
    partner_1: Optional[int]
    partner_2: Optional[int]
    created_at: Optional[float]


class Fun(commands.Cog):
    """Module for fun commands."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.stopwatches = {}
        self.text_flip = {}
        self.active_tasks = []
        self.osu_id = 12187
        self.genshin_client = genshin.Client()
        self.bigmoji_cache = MelanieCache("bigmoji")
        self.locks = default_lock_cache()
        self.video_semaphore = asyncio.BoundedSemaphore(2)
        self.genshin_client.set_cookies(GENSHIN_COOKIES)
        self.osu_secret = "pEeJaweoHQRV5rVrNiHkpzryD7J494qXU6hYSnsE"
        self.config = Config.get_conf(self, identifier=2502, force_registration=True)
        self.config.register_user(**UserSettings().dict())
        self.config.register_guild(ogu_role=None, auto_text=True)

        self.config.init_custom("MARRIAGE", 1)
        self.config.register_custom("MARRIAGE", **MarriageSettings().dict())
        self.prefix_cache: dict[int, tuple] = {}
        self.htx = httpx.AsyncClient(http2=True)
        self.update_locks = defaultdict(asyncio.Lock)
        self.stt_cache = LRU(5000)
        self.ogu_cache = {}
        self.session = aiohttp.ClientSession()
        self.bigmoji_sem = asyncio.BoundedSemaphore(4)

        self.genshin_characters: dict[str, str] = {}
        create_task(self.build_custom_prefix_cache())
        create_task(self.build_genshin_data())
        create_task(self.bigmoji_cache.cull(retry=True))

    def cog_unload(self) -> None:
        cancel_tasks(self.active_tasks)
        self.bot.ioloop.spawn_callback(self.session.close)

        self.bot.ioloop.spawn_callback(self.htx.aclose)

    def find_character(self, name: str) -> str:
        return extractOne(name, self.genshin_characters.keys(), scorer=ratio)[0]

    async def build_genshin_data(self) -> None:
        r = await self.htx.get("https://api.genshin.dev/characters")
        names = orjson.loads(r.content)
        for n in names:
            clean: str = default_process(n)
            clean = clean.replace(" ", "")
            self.genshin_characters[default_process(clean)] = n

    def marriage_key(self, user1, user2) -> str:
        if isinstance(user1, discord.Member):
            user1 = user1.id

        if isinstance(user2, discord.Member):
            user2 = user2.id

        user2 = int(user2)
        user1 = int(user1)

        return xxhash.xxh32_hexdigest(f"{user1 + user2}")

    @commands.max_concurrency(1, commands.BucketType.user)
    @commands.command()
    async def marry(self, ctx: commands.Context, user: discord.Member):
        """Marry someone."""
        if user == ctx.author:
            return await ctx.send("You cannot marry yourself...")

        key = self.marriage_key(ctx.author, user)

        settings: MarriageSettings = MarriageSettings.parse_obj(await self.config.custom("MARRIAGE", key).all())
        if settings.created_at:
            return await ctx.send("You two are already married!")

        user_settings: UserSettings = UserSettings.parse_obj(await self.config.user(user).all())
        if user_settings.marriage_key:
            return await ctx.send(embed=make_e(f"**{user.mention}** is already married!", 2))

        user_settings2: UserSettings = UserSettings.parse_obj(await self.config.user(ctx.author).all())
        if user_settings2.marriage_key:
            return await ctx.send(embed=make_e("You're already married!", 2))

        await ctx.send(f"{user.mention}\n{ctx.author.mention} wants to marry you. What do you say? (yes/no)")

        def check(m: discord.Message):
            if m.channel.id == ctx.channel.id and m.author.id == user.id:
                if "yes" in m.content.lower():
                    return m
                if "no" in m.content.lower():
                    return m

            if m.channel.id == ctx.channel.id and m.author.id == ctx.author.id:
                if "no" in m.content.lower():
                    return m

                if "cancel" in m.content.lower():
                    return m

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60)

            response: str = msg.content.lower()

        except TimeoutError:
            response = "no"
            return await ctx.send(embed=make_e(f"**{ctx.author.mention}**: Sorry. They took too long to respond", 2))

        if "yes" in response:
            await self.config.user(user).marriage_key.set(key)
            await self.config.user(ctx.author).marriage_key.set(key)
            settings.created_at = time.time()
            settings.partner_1 = ctx.author.id
            settings.partner_2 = user.id
            async with self.config.custom("MARRIAGE", key).all() as _settings:
                _settings.update(settings.dict())

            await ctx.send(
                embed=discord.Embed(
                    color=int("dd2e44", 16),
                    description=f":revolving_hearts: **{user.display_name}** and **{ctx.author.display_name}** are now married :wedding:",
                ),
            )

        else:
            return await ctx.send(embed=make_e(f"**{ctx.author.mention}**: Sorry. They declined.", 2))

    @commands.command()
    async def divorce(self, ctx: commands.Context):
        """End your marriage."""
        user_settings2: UserSettings = UserSettings.parse_obj(await self.config.user(ctx.author).all())
        if not user_settings2.marriage_key:
            return await ctx.send(embed=make_e("You're not married!", 2))
        settings: MarriageSettings = MarriageSettings.parse_obj(await self.config.custom("MARRIAGE", user_settings2.marriage_key).all())
        partner_id = settings.partner_2 if settings.partner_1 == ctx.author.id else settings.partner_1

        partner = ctx.guild.get_member(partner_id) or await self.bot.fetch_user(partner_id)

        conf, msg = await yesno(":broken_heart: " + (f"Divorce **{partner.display_name}**?" if partner else "Divorce?"))

        if conf:
            await self.config.user(ctx.author).marriage_key.set(None)
            await self.config.user(partner).marriage_key.set(None)
            await self.config.custom("MARRIAGE", user_settings2.marriage_key).clear()

            return await ctx.tick()

    @commands.command()
    async def marriage(self, ctx: commands.Context, member: discord.Member | discord.User | None = None):
        """Check your marriage status."""
        member = member or ctx.author

        user_settings2: UserSettings = UserSettings.parse_obj(await self.config.user(ctx.author).all())
        if not user_settings2.marriage_key:
            return await ctx.send(embed=make_e("You're not married!", 2))
        settings: MarriageSettings = MarriageSettings.parse_obj(await self.config.custom("MARRIAGE", user_settings2.marriage_key).all())
        partner_id = settings.partner_2 if settings.partner_1 == ctx.author.id else settings.partner_1

        partner = ctx.guild.get_member(partner_id) or await self.bot.fetch_user(partner_id)
        marriage_date = arrow.get(settings.created_at)

        length = humanize.naturaldelta(arrow.utcnow().timestamp() - marriage_date.timestamp(), months=False)
        await ctx.send(
            embed=discord.Embed(
                color=int("f4abba", 16),
                description=(
                    f":wedding: {'You have' if member == ctx.author else f'**{member}** has'} been married to "
                    + (f"**{partner.display_name}**" if partner else "someone")
                    + f" for **{length}**"
                ),
            ),
        )

    @commands.command()
    async def door(self, ctx: commands.Context, user: discord.User):
        """Where's the door?."""
        return await asyncio.gather(ctx.send(f"{user.mention} 👉🏿 🚪"), ctx.message.delete())

    async def build_custom_prefix_cache(self, user_id: typing.Optional[int] = None) -> None:
        if user_id:
            custom_prefix: str = await self.config.user_from_id(user_id).custom_prefix()
            if not custom_prefix:
                del self.prefix_cache[user_id]
            else:
                self.prefix_cache[user_id] = get_case_values(custom_prefix)
        else:
            with log.catch(exclude=asyncio.CancelledError):
                users = await self.config.all_users()
                for uid, data in users.items():
                    if custom_prefix := data.get("custom_prefix"):
                        self.prefix_cache[uid] = get_case_values(custom_prefix)

            size = len(msgpack.packb(self.prefix_cache))
            log.success(f"Loaded {len(self.prefix_cache)} / {bytes2human(size, 3)} custom prefixes")

    @commands.cooldown(3, 5, commands.BucketType.guild)
    @commands.command(usage="amber hello, this is speech coming from a bot")
    async def speak(self, ctx: commands.Context, *, voice_and_msg: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Perform TTS command and play it in VC."""
        if not ctx.author.voice:
            return await ctx.send(embed=make_e("You must be in a current vc to use speech synthesis", 2))

        me: discord.Member = ctx.guild.me
        if me.voice and ctx.author.voice.channel.id != me.voice.channel.id:
            return await ctx.send(embed=make_e(f"I'm already in {me.voice.channel.mention}. Move me to your vc or go ;d to disconnect", 2))

        if ctx.guild.id == 836671067853422662 and await self.bot.redis.ratelimited("trait_rl:836671067853422662", 4, 320):
            return await ctx.send(
                embed=make_e(
                    "This server has increased rate limits and has exceeded the threshold for this command at the moment.",
                    3,
                    tip=f"Ratelimit for this command @ {ctx.guild} will reset in approximately 5 minutes.",
                ),
            )
        speaker = SPEAKERS["eric"]

        voice_and_msg = " ".join(voice_and_msg.split())
        if len(voice_and_msg) > 9000:
            return await ctx.send(embed=make_e("Text requests must be less than 9000 characters", 3))
        splits = voice_and_msg.split(" ")
        if len(splits) > 1:
            _voice = splits[0].lower().strip()
            if _voice in SPEAKERS:
                voice_and_msg = voice_and_msg.replace(_voice, "")
                speaker = SPEAKERS[_voice]

        curl = get_curl()

        async with asyncio.timeout(60):
            url = url_concat("https://dev.melaniebot.net/api/speech/tts", {"file": True, "user_id": ctx.author.id})
            payload = TTSTranslationRequest(text=voice_and_msg, speaker_name=speaker["name"])
            r = await curl.fetch(url, headers=SHARED_API_HEADERS, body=payload.jsonb(), method="POST")
            data = orjson.loads(r.body)
            if "url" not in data:
                return await ctx.send(embed=make_e("Bad response from the API. Try again later", 2))
            url = data["url"]

            async with asyncio.timeout(30):
                audio: AudioMeta = self.bot.get_cog("Audio")
                audio.silence_flag.set(url)
                await self.bot.redis.set(f"silent_alert:{ctx.channel.id}", time.time(), ex=3)
                await ctx.invoke(ctx.bot.get_command("play"), query=url)

            await ctx.message.add_reaction("🗣️")

    @commands.command()
    async def skull(self, ctx: commands.Context, message: discord.Message = None):
        """Skull me up, G."""
        HOME_GUILD: discord.Guild = self.bot.get_guild(915317604153962546)

        r = HOME_GUILD.get_role(1069023590806192169)

        if ctx.author not in r.members:
            return await ctx.send(embed=make_e("This command is **restricted** to **whitelisted** melanie paid+ users.", status=3))

        if not message:
            channel: discord.TextChannel = ctx.channel

            if ctx.message.reference:
                message = ctx.message.reference.cached_message or await channel.fetch_message(ctx.message.reference.message_id)

            else:
                history = await channel.history(limit=1, before=ctx.message).flatten()
                message = history[0]
        await ctx.message.delete(delay=0.01)
        await self.bot.redis.set(f"skull_react:{message.id}", tuuid.tuuid())
        skulls: list[discord.Emoji] = [e for e in HOME_GUILD.emojis if "skul" in str(e).lower()]
        random.shuffle(skulls)
        for emote in skulls:
            try:
                await message.add_reaction(emote)
            except discord.HTTPException:
                break

    @commands.command()
    async def nerd(self, ctx: commands.Context, message: discord.Message = None):
        """Fuck up nerd.."""
        HOME_GUILD: discord.Guild = self.bot.get_guild(915317604153962546)

        r = HOME_GUILD.get_role(1069023590806192169)
        if ctx.author not in r.members:
            return await ctx.send(embed=make_e("This command is **restricted** to **whitelisted** melanie paid+ users.", status=3))

        if not message:
            channel: discord.TextChannel = ctx.channel

            if ctx.message.reference:
                message = ctx.message.reference.cached_message or await channel.fetch_message(ctx.message.reference.message_id)

            else:
                history = await channel.history(limit=1, before=ctx.message).flatten()
                message = history[0]
        await ctx.message.delete(delay=0.01)

        _reaction_guild = HOME_GUILD

        await self.bot.redis.set(f"l_react:{message.id}", tuuid.tuuid())
        ls: list[discord.Emoji] = [e for e in _reaction_guild.emojis if "nerd" in str(e).lower()]
        random.shuffle(ls)
        for emote in ls:
            try:
                await message.add_reaction(emote)
            except discord.HTTPException:
                break

    @commands.cooldown(1, 4, commands.BucketType.guild)
    @commands.command(usage="https://dev.melaniebot.net")
    async def screenshot(self, ctx: commands.Context, *, url: str, full_page: bool = False):
        """Generate a live screenshot of any web URL.

        Attempts to load the entire page and scroll to the bottom

        """
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        start = time.time()
        _url = yarl.URL(url)
        if not await can_name_resolve(_url.host):
            return await ctx.send(embed=make_e(f"**{_url.host}** does not resolve!", 2))
        url = str(_url)
        async with asyncio.timeout(120):
            async with ctx.typing():
                r = await self.bot.curl.fetch(
                    CurlRequest(
                        url_concat("https://dev.melaniebot.net/api/web/screenshot", {"user_id": ctx.author.id, "url": str(_url), "full_page": full_page}),
                        headers=SHARED_API_HEADERS,
                    ),
                    raise_error=False,
                )
                if r.code == 400:
                    return await ctx.send(embed=make_e("NSFW websites are not allowed to be screenshot", 3))
                elif r.code == 429:
                    return await ctx.send(embed=make_e("You're currently ratelimited from making requests. Try again in a few minutes", 3))

                mime = str(filetype.guess_mime(r.body))
                if "image" not in mime:
                    return await ctx.send(embed=make_e("Unable to screenshot that website at the moment", 3))

                lookup = await get_image_colors2(r.body)
                dur = int(time.time() - start)
                embed = discord.Embed()
                embed.url = url
                embed.title = url.removesuffix("/")
                embed.set_footer(text=f"{fmtseconds(dur)} | melanie", icon_url=footer_gif)
                if lookup:
                    embed.color = lookup.dominant.decimal
                embed.set_image(url="attachment://screenshot.png")

            return await ctx.send(file=discord.File(r.buffer, filename="screenshot.png"), embed=embed)

    @commands.command(aliases=["stt", "txt", "text"])
    async def transcribe(self, ctx: commands.Context, clip: Optional[Union[AudioVideoFindeer, str]], translate: bool = False):
        """Perform Speech to Text of an audio or video clip."""
        if clip is None:
            clip = await AudioVideoFindeer().search_for_images(ctx)
        if not isinstance(clip, str):
            clip = clip[0]

        typer = nullcontext() if ctx.message.id in self.stt_cache else ctx.typing()
        async with typer, asyncio.timeout(80):
            r = await self.bot.curl.fetch(
                url_concat("https://dev.melaniebot.net/api/speech/stt", {"user_id": str(ctx.author.id), "url": clip, "translate": str(translate).lower()}),
                headers=SHARED_API_HEADERS,
            )

            s = STTResult.parse_raw(r.body)
            if not s.display_text:
                if ctx.message.id in self.stt_cache:
                    return

                return await ctx.send(embed=make_e("I couldn't find hear any text in that clip", 3))
            text_value = s.display_text
            text_value = text_value.capitalize()
            for match in INVITE_RE.findall(text_value):
                text_value = text_value.replace(match, "(redacted)")
            for link in find_all_links(text_value):
                text_value = text_value.replace(str(link), "(redacted)")
            em = discord.Embed()
            em.description = f">>> {textwrap.shorten(text_value, width=4500)}"
            await ctx.send(embed=em)

    @commands.cooldown(1, 4, commands.BucketType.guild)
    @commands.command(usage="amber hello, this is speech coming from a bot")
    async def tts(self, ctx: commands.Context, *, voice_and_msg: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Make an MP3 text to speech. Use the default voice Eric or pick one of
        the custom ones below.

        Choose between speakers: `prabhat, tim, amber, ana, aria,
        ashley, brandon, christopher, cora, davis, elizabeth, eric, guy,
        jacob, jane, jason, jenny, michelle, monica, nancy, sara, tony,
        maisie, abbi`

        """
        speaker = SPEAKERS["ana"]
        voice_and_msg = " ".join(voice_and_msg.split())
        if len(voice_and_msg) > 5000:
            return await ctx.send(embed=make_e("Text requests must be less than 5000 characters", 3))
        splits = voice_and_msg.split(" ")
        if len(splits) > 1:
            _voice = splits[0].lower().strip()
            if _voice in SPEAKERS:
                voice_and_msg = voice_and_msg.replace(_voice, "")
                speaker = SPEAKERS[_voice]

        async with ctx.typing():
            curl = get_curl()
            async with asyncio.timeout(60):
                payload = TTSTranslationRequest(text=voice_and_msg, speaker_name=speaker["name"])
                r = await curl.fetch(
                    url_concat(
                        "https://dev.melaniebot.net/api/speech/tts",
                        {"user_id": ctx.author.id, "output_format": "ogg"},
                    ),
                    headers=SHARED_API_HEADERS,
                    body=payload.jsonb(),
                    method="POST",
                )

                url = orjson.loads(r.body)["url"]
                r = await curl.fetch(url)

                filename = "melanieTTS.ogg"
                try:
                    return await ctx.reply(file=discord.File(io.BytesIO(r.body), filename=filename))
                except discord.HTTPException:
                    return await ctx.send(file=discord.File(io.BytesIO(r.body), filename=filename))

    @commands.cooldown(1, 4, commands.BucketType.guild)
    @commands.command()
    async def react(self, ctx: commands.Context, msg: str, message: Optional[discord.Message]) -> None:
        """Add letter(s) as reaction to previous message.

        `[message]` Can be a message ID from the current channel, a jump
        URL, or a channel_id-message_id from shift + copying ID on the
        message.

        """
        if message is None:
            async for messages in ctx.channel.history(limit=2):
                message = messages

        reactions = []
        non_unicode_emoji_list = []
        react_me = ""
        # this is the string that will hold all our unicode converted characters from msg

        # replace all custom server emoji <:emoji:123456789> with "<" and add emoji ids to non_unicode_emoji_list
        emotes = re.findall(r"<a?:(?:[a-zA-Z0-9]+?):(?:[0-9]+?)>", msg.lower())
        react_me = re.sub(r"<a?:([a-zA-Z0-9]+?):([0-9]+?)>", "", msg.lower())

        for emote in emotes:
            reactions.append(discord.utils.get(self.bot.emojis, id=int(emote.split(":")[-1][:-1])))
            non_unicode_emoji_list.append(emote)

        if has_dupe(non_unicode_emoji_list):
            return await ctx.send(
                "You requested that I react with at least two of the exact same specific emoji. I'll try to find alternatives for alphanumeric text, but if you specify a specific emoji must be used, I can't help.",
            )

        react_me_original = react_me
        # we'll go back to this version of react_me if prefer_combine
        # is false but we can't make the reaction happen unless we combine anyway.

        if has_dupe(react_me):
            # there's a duplicate letter somewhere, so let's go ahead try to fix it.
            react_me = replace_combos(react_me)
            react_me = replace_letters(react_me)
            if has_dupe(react_me):  # check if we were able to solve the dupe
                react_me = react_me_original
                react_me = replace_combos(react_me)
                react_me = replace_letters(react_me)
                if has_dupe(react_me):
                    # this failed too, so there's really nothing we can do anymore.
                    return await ctx.send("Failed to fix all duplicates. Cannot react with this string.")

            for char in react_me:
                if char in "0123456789":
                    reactions.append(emoji_dict[char][0])
                elif char != "⃣":  # </3
                    reactions.append(char)
        else:  # probably doesn't matter, but by treating the case without dupes seperately, we can save some time
            for char in react_me:
                if char in "abcdefghijklmnopqrstuvwxyz0123456789!?":
                    reactions.append(emoji_dict[char][0])
                else:
                    reactions.append(char)

        if message.channel.permissions_for(ctx.me).add_reactions:
            with contextlib.suppress(discord.HTTPException):
                for reaction in reactions:
                    await message.add_reaction(reaction)
        if message.channel.permissions_for(ctx.me).manage_messages:
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.delete()
        else:
            await ctx.tick()

    @commands.command()
    async def urban(self, ctx, *, word):
        """Search the Urban Dictionary.

        This uses the unofficial Urban Dictionary API.

        """
        try:
            params = {"term": str(word).lower()}

            url = "https://api.urbandictionary.com/v0/define"

            headers = {"content-type": "application/json"}

            async with self.bot.aio.get(url, headers=headers, params=params) as response:
                data = await response.json()

        except aiohttp.ClientError:
            await ctx.send("No Urban Dictionary entries were found, or there was an error in the process.")
            return

        if data.get("error") != 404:
            if not data.get("list"):
                return await ctx.send("No Urban Dictionary entries were found.")
            if await ctx.embed_requested():
                # a list of embeds
                embeds = []
                for ud in data["list"]:
                    embed = discord.Embed(color=await ctx.embed_color())
                    title = f"{ud['word'].capitalize()} by {ud['author']}"
                    if len(title) > 256:
                        title = f"{title[:253]}..."
                    embed.title = title
                    embed.url = ud["permalink"]

                    description = ("{definition}\n\n**Example:** {example}").format(**ud)
                    if len(description) > 2048:
                        description = f"{description[:2045]}..."
                    embed.description = description

                    embed.set_footer(text=("{thumbs_down} Down / {thumbs_up} Up, Powered by Urban Dictionary.").format(**ud))
                    embeds.append(embed)

                if embeds is not None and embeds:
                    await menu(ctx, pages=embeds, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30)
            else:
                messages = []
                for ud in data["list"]:
                    ud.setdefault("example", "N/A")
                    message = (
                        "<{permalink}>\n {word} by {author}\n\n{description}\n\n{thumbs_down} Down / {thumbs_up} Up, Powered by Urban Dictionary."
                    ).format(word=ud.pop("word").capitalize(), description="{description}", **ud)
                    max_desc_len = 2000 - len(message)

                    description = ("{definition}\n\n**Example:** {example}").format(**ud)
                    if len(description) > max_desc_len:
                        description = f"{description[: max_desc_len - 3]}..."

                    message = message.format(description=description)
                    messages.append(message)

                if messages is not None and messages:
                    await menu(ctx, pages=messages, controls=DEFAULT_CONTROLS, message=None, page=0, timeout=30)
        else:
            await ctx.send("No Urban Dictionary entries were found, or there was an error in the process.")

    @commands.command()
    async def read(self, ctx: commands.Context, image: ImageFinder = None, voice: str = "eric"):
        """Perform OCR on an image, and then generate an audio file of its output."""
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        url = str(image[0])
        async with asyncio.timeout(45):
            curl = get_curl()
            async with ctx.typing():
                try:
                    r = await curl.fetch(
                        CurlRequest(
                            url_concat("https://dev.melaniebot.net/api/ai/ocr", {"user_id": ctx.author.id}),
                            headers=SHARED_API_HEADERS,
                            body=orjson.dumps({"url": url}),
                            method="POST",
                        ),
                    )

                except CurlError:
                    return await ctx.send(embed=make_e("No text could be extracted from that image. Try another image?", 2))
                data = OCRReadResponse.parse_raw(r.body)
                if not data.display_text:
                    return await ctx.send(embed=make_e("No text could be extracted from that image. Try another image?", 2))

                return await self.tts(ctx, voice_and_msg=data.display_text.replace("\n", " "))

    @commands.command()
    async def ocr(self, ctx: commands.Context, image: Optional[ImageFinder]):
        """Perform OCR on an image.

        (Image to text)

        """
        if not image:
            image = await ImageFinder().search_for_images(ctx)
        url = str(image[0])
        async with asyncio.timeout(45):
            curl = get_curl()
            async with ctx.typing():
                try:
                    r = await curl.fetch(
                        url_concat("https://dev.melaniebot.net/api/ai/ocr", {"user_id": ctx.author.id}),
                        headers=SHARED_API_HEADERS,
                        body=orjson.dumps({"url": url}),
                        method="POST",
                    )
                except CurlError:
                    return await ctx.send(embed=make_e("No text could be extracted from that image. Try another image?", 2))
                data = OCRReadResponse.parse_raw(r.body)
                if not data.lines:
                    return await ctx.send(embed=make_e("No text could be extracted from that image. Try another image?", 2))
                text_value = data.display_text
                for match in INVITE_RE.findall(text_value):
                    text_value = text_value.replace(match, "(redacted)")
                for link in find_all_links(text_value):
                    if "discord" in str(link):
                        text_value = text_value.replace(str(link), "redacted")
                if len(text_value) > 1000:
                    e = discord.Embed()
                    e.description = text_value
                    return await ctx.send(embed=e)
                return await ctx.send(text_value[:1200])

    @commands.command(aliases=["tp"])
    async def transparent(self, ctx: commands.Context, image: ImageFinder = None, alpha_matting: bool = False):
        """Generate a transparent version of an image."""
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        url = image[0]
        url = str(url)
        async with asyncio.timeout(45), ctx.typing():
            r = await self.bot.curl.fetch(url_concat("https://dev.melaniebot.net/api/ai/segment_bg", {"url": url}), headers=SHARED_API_HEADERS)
            ext = "." + guess_extension(r.body)
            name = f"melanieTransparent{ext}"

            return await ctx.send(file=discord.File(r.buffer, filename=name))

    @alru_cache
    async def generate_bigmoji(self, url, format):
        extension = ".png" if format in ("png", "svg") else ".gif"
        key = f"bigmoji_{xxhash.xxh32_hexdigest(f'{url}:{format}')}{extension}"
        cache_url = f"https://cache2.hurt.af/{key}"
        async with self.locks[key]:
            curl = get_curl()
            try:
                r = await curl.fetch(cache_url)
                data = r.body
            except CurlError:
                exe: ExecutionsTracker = self.bot.get_cog("ExecutionsTracker")
                data = await generate_bigmoji4(url, format)
                mime = guess_mime(data)
                spawn_task(exe.s3.put_object(Key=key, Bucket="cache2", Body=data, ContentType=mime), self.active_tasks)

        return data

    async def get_uncached_emotes_tasks(self, desired_type=None) -> list[typing.Awaitable]:
        uncached = []
        sem = asyncio.BoundedSemaphore(1000)

        async def check(cache_url, url):
            async with sem, self.bot.aio.head(cache_url) as r:
                if not r.ok:
                    uncached.append(self.generate_bigmoji(url, format))

        async with asyncio.TaskGroup() as tg:
            for guild in sorted(self.bot.guilds, key=lambda g: bool(g.get_member(728095627757486081)), reverse=True):
                log.info(guild)
                for e in guild.emojis:
                    url, format, name = extract_url_format(str(e))
                    if desired_type and format != desired_type:
                        continue
                    extension = ".png" if format in ("png", "svg") else ".gif"
                    key = f"bigmoji_{xxhash.xxh32_hexdigest(f'{url}:{format}')}{extension}"
                    cache_url = f"https://cache2.hurt.af/{key}"

                    tg.create_task(check(cache_url, url))
                    await checkpoint()

        log.success("Reporting {} out of {} uncached emotes needing to be rendered ", len(uncached), len(self.bot.emojis))
        return uncached

    @commands.command()
    async def draw(self, ctx, *, prompt: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Use machine learning to create images based of a prompt."""
        async with asyncio.timeout(60):
            prompt = prompt.lower()
            async with ctx.typing():
                url = url_concat("https://dev.melaniebot.net/api/ai/avatar", {"idea": prompt, "user_id": ctx.author.id})
                curl = get_curl()
                r = await curl.fetch(url, headers=SHARED_API_HEADERS, raise_error=False)
                if r.error:
                    await ctx.send(
                        embed=make_e(
                            "I'm not able to generate an image of that!",
                            2,
                            tip="i wasn't trained on nsfw and have limited knowledge of named people.",
                        ),
                    )
                    raise r.error
                data = AIImageGenerationResponse.parse_raw(r.body)
                embed = discord.Embed()
                embed.title = "ai image generation"
                embed.description = f'_prompt: "{prompt}"_'
                embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                lookup = await get_image_colors2(data.url)
                if lookup:
                    embed.color = lookup.dominant.decimal
                embed.set_image(url=data.url)
                await checkpoint()

            try:
                return await ctx.reply(embed=embed)
            except discord.HTTPException:
                return await ctx.send(embed=embed)

    @commands.command()
    async def draw2(self, ctx, *, prompt: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Use machine learning to create images based of a prompt."""
        async with asyncio.timeout(60):
            prompt = prompt.lower()
            async with ctx.typing():
                url = url_concat("https://dev.melaniebot.net/api/ai/cyberpunk", {"idea": prompt, "user_id": ctx.author.id})
                curl = get_curl()
                r = await curl.fetch(url, headers=SHARED_API_HEADERS, raise_error=False)
                if r.error:
                    await ctx.send(
                        embed=make_e(
                            "I'm not able to generate an image of that!",
                            2,
                            tip="i wasn't trained on nsfw and have limited knowledge of named people.",
                        ),
                    )
                    raise r.error
                data = AIImageGenerationResponse.parse_raw(r.body)
                embed = discord.Embed()
                embed.title = "ai image generation"
                embed.description = f'_prompt: "{prompt}"_'
                embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
                lookup = await get_image_colors2(data.url)
                if lookup:
                    embed.color = lookup.dominant.decimal
                embed.set_image(url=data.url)
                await checkpoint()
            try:
                return await ctx.reply(embed=embed)
            except discord.HTTPException:
                return await ctx.send(embed=embed)

    @commands.command()
    async def draw3(self, ctx, *, prompt: commands.clean_content(use_nicknames=True, remove_markdown=True, fix_channel_mentions=True)):
        """Use machine learning to create images based of a prompt."""
        async with asyncio.timeout(60):
            prompt = prompt.lower()
            async with ctx.typing():
                url = url_concat("https://dev.melaniebot.net/api/ai/creative", {"idea": prompt, "user_id": ctx.author.id})
                curl = get_curl()
                r = await curl.fetch(url, headers=SHARED_API_HEADERS, raise_error=False)
                if r.error:
                    await ctx.send(
                        embed=make_e(
                            "I'm not able to generate an image of that!",
                            2,
                            tip="i wasn't trained on nsfw and have limited knowledge of named people.",
                        ),
                    )
                    raise r.error
                data = AIImageGenerationResponse.parse_raw(r.body)
                embed = discord.Embed()
                lookup = await get_image_colors2(data.url)
                if lookup:
                    embed.color = lookup.dominant.decimal
                embed.title = "ai image generation"
                embed.description = f'_prompt: "{prompt}"_'
                embed.set_footer(text="melanie ^_^", icon_url=footer_gif)

                embed.set_image(url=data.url)
            try:
                return await ctx.reply(embed=embed)
            except discord.HTTPException:
                return await ctx.send(embed=embed)

    @checks.has_permissions(manage_messages=True)
    @commands.command(hidden=True)
    async def pingall(self, ctx: commands.Context):
        """Ping everyone. Individually.

        Requires trusted admin status.

        """
        anti: AntiNuke = self.bot.get_cog("AntiNuke")
        guild: discord.Guild = ctx.guild
        if not await anti.is_trusted_admin(ctx):
            return await ctx.send(embed=make_e("Only trusted admins may ping everyone.", tip="use ;an trust to add someone", status=3))

        confirmed, _msg = await yesno("This command will ping everyone in the server.", "Are you sure you want to do this?")
        if confirmed:
            if ctx.author.id not in self.bot.owner_ids and await self.bot.redis.ratelimited(f"guildping:{guild.id}", 1, 86400):
                return await ctx.send(embed=make_e("Pingall can only be used once per day.", status=3))
            guild: discord.Guild = ctx.guild
            mentions = " ".join(m.mention for m in guild.members if not m.bot)
            await asyncio.gather(*[ctx.send(chunk, delete_after=0.5) for chunk in textwrap.wrap(mentions, 1950)])

        #                 async for result in paginator.paginate(Bucket="gif"):

    @commands.command(name="bigmoji", aliases=["e", "bigemoji"])
    async def bigmoji(self, ctx, *, emoji):
        """Post a large .png of an emoji."""
        async with ctx.typing(), asyncio.timeout(30):
            try:
                (url, format, name) = extract_url_format(emoji)
            except IndexError:
                return await ctx.send("That doesn't look like an emoji to me!")

            img = await self.generate_bigmoji(url, format)
            if not img:
                return await ctx.send(embed=make_e("I had an issue downloading that emote", 3))
            try:
                await ctx.send(file=discord.File(io.BytesIO(img), name))
            except discord.errors.HTTPException:
                await ctx.send("The Image file size is greater than this server's boost tier. ")

    @commands.command()
    async def customprefix(self, ctx: commands.Context, *, prefix: str = None):
        """Set your custom prefix to be used across all servers you share with
        Melanie.

        Set the prefix to `none` to remove your custom prefix.

        """
        if not prefix:
            custom = await self.config.user(ctx.author).custom_prefix()
            if custom:
                return await ctx.send(
                    embed=make_e(
                        f"Your custom prefix is **{custom}**",
                        status="info",
                        tip="re-run this cmd with a new prefix to change it, or set it to 'none' to remove it. ",
                    ),
                )
            else:
                return await ctx.send_help()

        if len(prefix) > 10:
            return await ctx.send(embed=make_e("The custom prefix needs to be less than 10 characters", 2))

        if prefix.lower() == "none":
            prefix = None

        async def set_prefix_backround() -> None:
            await self.config.user(ctx.author).custom_prefix.set(prefix)
            await self.build_custom_prefix_cache(ctx.author.id)
            log.warning(f"Custom prefix for {ctx.author} set to {prefix}")

        create_task(set_prefix_backround())
        embed = make_e(f"Your custom prefix has been set to **{prefix}**") if prefix else make_e("Your custom prefix has been removed")
        return await ctx.send(embed=embed)

    @commands.cooldown(2, 3, commands.BucketType.user)
    @commands.guild_only()
    @commands.group(name="genshin", invoke_without_command=True)
    async def genshin(self, ctx: commands.Context, member: discord.Member = None):
        """Show stats from Genshin."""
        if not member:
            member = ctx.author
        async with asyncio.timeout(10), ctx.typing(), self.config.user(member).all() as config_settings:
            settings = UserSettings.parse_obj(config_settings)
            genshin_id = settings.genshin_id
            if not genshin_id:
                return await ctx.send(embed=make_e(f"{member.mention} has no Genshin user id set yet!", 2))

            g = await self.genshin_client.get_genshin_user(genshin_id)

            # if settings.genshin_user_history:
            #     if orjson.dumps(last_profile.dict()) != orjson.dumps(g.dict()):

            embed = discord.Embed()
            embed.title = f"{g.info.nickname} (uid {genshin_id})".lower()
            top_character = g.characters[0]

            api_character_name = f"{top_character.name}-{top_character.element}" if top_character.name == "Traveler" else top_character.name

            api_character_name = api_character_name.split(" ")[0]

            api_character_name = api_character_name.lower()

            if api_character_name == "hu":
                api_character_name = "hu-tao"

            if api_character_name == "sangonomiya":
                api_character_name = "kokomi"

            if api_character_name == "kamisato ayaka":
                api_character_name = "ayaka"

            character_img = f"https://api.genshin.dev/characters/{api_character_name}/gacha-splash"

            region = g.info.server

            if region == "os_euro":
                region = "europe"
            if region == "os_asia":
                region = "asia"
            if region == "os_usa":
                region = "america"

            embed.add_field(name="📊 adventure rank", value=g.info.level, inline=True)
            embed.add_field(name="🌟 achievements", value=g.stats.achievements, inline=True)
            embed.add_field(name="🌍 region", value=region, inline=True)

            if g.info.icon and g.info.icon.startswith("https"):
                thumb_url = g.info.icon
            else:
                thumb_url = f"https://api.genshin.dev/characters/{api_character_name}/talent-burst"

            try:
                colors = await get_image_colors2(character_img)
                embed.color = colors.dominant.decimal
                embed.set_thumbnail(url=thumb_url)
                embed.set_image(url=character_img)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(f"Unable to get image colors for {api_character_name}")

            embed.add_field(name="days active", value=g.stats.days_active, inline=True)
            embed.add_field(name="characters", value=g.stats.characters, inline=True)
            embed.add_field(name="unlocked domains", value=g.stats.unlocked_domains, inline=True)
            embed.add_field(name="unlocked waypoints", value=g.stats.unlocked_waypoints, inline=True)
            embed.add_field(name="top character", value=f"{top_character.name} lv: {top_character.level} ({top_character.element}) ".lower())

            embed.set_footer(text="melanie | genshin stats", icon_url=footer_gif)

            config_settings.update(settings.dict())

            return await ctx.send(embed=embed)

    @genshin.command(name="character", aliases=["char", "c"])
    async def genshin_char(self, ctx: commands.Context, *, name: str = None):
        """Find a genshin character."""
        async with asyncio.timeout(10), ctx.typing():
            if not name:
                name_str = "".join(f"{n}, " for n in self.genshin_characters)

                embed = make_e(f"Here are then Genshin characters you can query\n\n{name_str}", status="info")
                return await ctx.send(embed=embed)

            name = self.find_character(name)

            item = await GenshinCharacter.from_name(name, self.htx)

            return await ctx.send(embed=await item.generate_embed())

    @genshin.command(name="set")
    async def genshin_set(self, ctx: commands.Context, user_id: int):
        """Set your Genshin username for Melanie to remember."""
        try:
            await self.genshin_client.get_genshin_user(user_id)
        except DataNotPublic:
            embed = make_e("Your data is not public! Go to https://www.hoyolab.com/setting/privacy and enable public information", 2)
            embed.set_image(url="https://cdn.discordapp.com/attachments/940528128755912704/995215735511060541/unknown.png")
            await asyncio.sleep(0.1)
            return await ctx.send(embed=embed)

        await self.config.user(ctx.author).genshin_id.set(user_id)
        return await ctx.send(embed=make_e(f"I've set **{user_id}** as your Genshin User ID."))

    @commands.cooldown(2, 3, commands.BucketType.guild)
    @commands.guild_only()
    @commands.group(name="osu", aliases=["osulookup"], invoke_without_command=True)
    async def osu(self, ctx: commands.Context, *, username: Union[discord.Member, str] = None):
        """Fetch basic infomratin on a user's osu username."""
        async with asyncio.timeout(20):
            if username and isinstance(username, discord.Member):
                username = await self.config.user(username).osu_username()
                if not username:
                    return await ctx.send(embed=make_e("No osu username for that member", status=2))

            if not username:
                username = await self.config.user(ctx.author).osu_username()
            if not username:
                return await ctx.send(embed=make_e("Either provide a username or set your username with `;osu set`", status=2))
            async with ctx.typing():
                res: OsuUser = await get_osu_user(self.osu_id, self.osu_secret, username)

                if not res or not res.join_date:
                    return await ctx.send(embed=make_e("Invalid osu username", status=2))

                color = await get_image_colors2(res.avatar_url)
                em = discord.Embed(title=username, color=discord.Color(color.dominant.decimal))
                join_date = arrow.get(res.join_date).timestamp()
                last_visit = arrow.get(res.last_visit).timestamp()
                join_date_str = f"<t:{int(join_date)}:R>"
                playtime = f"{round(res.statistics.play_time / 60 /60,2)} hours"
                em.add_field(name="join date", value=join_date_str, inline=True)
                em.add_field(name="last seen", value=f"<t:{int(last_visit)}:R>", inline=True)
                em.add_field(name="global rank", value=f"#{intcomma(res.statistics.global_rank)}" if res.statistics.global_rank else "N/A", inline=True)
                em.add_field(name="level", value=res.statistics.level.current, inline=True)

                em.add_field(name="maximum combo", value=f"{res.statistics.maximum_combo}x", inline=True)

                em.add_field(name="play time", value=playtime, inline=True)

                em.set_footer(text="osu", icon_url="https://cdn.discordapp.com/attachments/918929359161663498/972287158952034305/osu.png")
                em.set_thumbnail(url=res.avatar_url)

                await ctx.send(embed=em)

    @osu.command(name="set")
    async def osu_set(self, ctx: commands.Context, *, username: str):
        """Set your osu username for Melanie to remember."""
        res: OsuUser = await get_osu_user(self.osu_id, self.osu_secret, username)
        if not res or not res.join_date:
            return await ctx.send(embed=make_e("Invalid osu username", status=2))
        await self.config.user(ctx.author).osu_username.set(username)
        return await ctx.send(embed=make_e(f"I've set **{username}** as your osu username."))

    @commands.command()
    async def makegif(
        self,
        ctx: commands.Context,
        image: VideoFinder = None,
        quality: int = 85,
        fps: int = 20,
        multiply_speed: int = 1,
    ):
        """Create a GIF from a video file.

        Processing time limited to 60 seconds and videos trimmed to 10 secons
        """
        timer = Timeit("conv")
        if self.video_semaphore.locked():
            return await ctx.send(embed=make_e("I'm busy converting other gif's at the moment.. try again later", status=2))

        async with self.video_semaphore:
            if image is None:
                image = await ImageFinder().search_for_images(ctx)
            url = str(image[0])
            embed = make_e("Converting file to gif..", status="info")
            status_msg = await ctx.send(embed=embed)
            async with ctx.typing(), asyncio.timeout(60):
                try:
                    try:
                        jobresult: GifRenderJobResult = await convert_to_gif(url, fps=fps, speed=multiply_speed, quality=quality)
                    except TimeoutError:
                        await ctx.send(embed=make_e("The GIF render job exceeded the maximum processing time. 🥺", 3))
                        raise

                    gif_size = jobresult.size
                    embed = make_e("video to gif conversion", tip="check ;help makegif to cutomize cmd options")
                    embed.add_field(name="quality ratio", value=f"{quality}/100")
                    embed.add_field(name="frame rate", value=f"{fps} fps", inline=True)
                    embed.add_field(name="speed multiplyer", value=f"{multiply_speed}x", inline=True)
                    embed.add_field(name="size", value=bytes2human(gif_size), inline=True)
                    embed.add_field(name="url", value=jobresult.url)
                    r = await self.bot.curl.fetch(jobresult.url)
                    dur = timer.done()
                    embed.set_footer(text=f"{dur}", icon_url=footer_gif)
                    await ctx.send(embed=embed, file=discord.File(r.buffer, filename="melanieGifconv.gif"))
                except TimeoutError:
                    log.warning(f"TIMEOUT processing GIF @ {ctx.guild} {ctx.author}")
                finally:
                    await status_msg.delete()

    @checks.has_permissions(manage_channels=True)
    @commands.command()
    async def autotxt(self, ctx: commands.Context):
        """Toggle Melanie's auto transcription of discord voice clips."""
        state = await self.config.guild(ctx.guild).auto_text()
        try:
            if state:
                await self.config.guild(ctx.guild).auto_text.set(False)
                return await ctx.send(embed=make_e("Auto transcription has been disabled"))
            else:
                await self.config.guild(ctx.guild).auto_text.set(True)
                return await ctx.send(embed=make_e("Auto transcription has been enabled"))
        finally:
            self.auto_text_enabled.cache_clear()

    @alru_cache(maxsize=None)
    async def auto_text_enabled(self, guild_id: int):
        return await self.config.guild_from_id(guild_id).auto_text()

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message: discord.Message):
        if not self.bot.is_ready():
            return
        if message.author.bot:
            return
        if self.bot.user.name != "melanie":
            return
        if len(message.attachments) != 1:
            return
        if message.attachments[0].filename != "voice-message.ogg":
            return
        if not message.guild:
            return
        if not await self.auto_text_enabled(message.guild.id):
            return
        ctx = await self.bot.get_context(message)
        ctx.via_event = True
        ctx.command = self.bot.get_command("stt")
        ctx.kwargs = {"clip": message.attachments[0].url}
        self.stt_cache[message.id] = message.attachments[0].url
        await self.bot.invoke(ctx)
