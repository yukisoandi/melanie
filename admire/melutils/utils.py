from __future__ import annotations

import datetime
import itertools
from io import BytesIO
from typing import Any

import discord
import regex as re
from melaniebot.core import Config
from melaniebot.core.bot import Melanie
from melaniebot.core.config import Config
from melaniebot.core.utils import AsyncIter
from PIL import Image
from regex.regex import Pattern

from melanie.curl import worker_download

from .common_variables import TWEMOJI_URL


def _(x):
    return x


DEFAULT_GLOBAL = {
    "status_emojis": {
        "mobile": 749067110931759185,
        "online": 749221433552404581,
        "away": 749221433095356417,
        "dnd": 749221432772395140,
        "offline": 749221433049088082,
        "streaming": 749221434039205909,
    },
    "badge_emojis": {
        "staff": 848556248832016384,
        "early_supporter": 706198530837970998,
        "hypesquad_balance": 706198531538550886,
        "hypesquad_bravery": 706198532998299779,
        "hypesquad_brilliance": 706198535846101092,
        "hypesquad": 706198537049866261,
        "verified_bot_developer": 706198727953612901,
        "bug_hunter": 848556247632052225,
        "bug_hunter_level_2": 706199712402898985,
        "partner": 848556249192202247,
        "verified_bot": 848561838974697532,
        "verified_bot2": 848561839260434482,
    },
}

VC_REGIONS: dict[str, Any] = {
    "vip-us-east": "__VIP__ US East " + "\U0001f1fa\U0001f1f8",
    "vip-us-west": "__VIP__ US West " + "\U0001f1fa\U0001f1f8",
    "vip-amsterdam": "__VIP__ Amsterdam " + "\U0001f1f3\U0001f1f1",
    "eu-west": "EU West " + "\U0001f1ea\U0001f1fa",
    "eu-central": "EU Central " + "\U0001f1ea\U0001f1fa",
    "europe": "Europe " + "\U0001f1ea\U0001f1fa",
    "london": "London " + "\U0001f1ec\U0001f1e7",
    "frankfurt": "Frankfurt " + "\U0001f1e9\U0001f1ea",
    "amsterdam": "Amsterdam " + "\U0001f1f3\U0001f1f1",
    "us-west": "US West " + "\U0001f1fa\U0001f1f8",
    "us-east": "US East " + "\U0001f1fa\U0001f1f8",
    "us-south": "US South " + "\U0001f1fa\U0001f1f8",
    "us-central": "US Central " + "\U0001f1fa\U0001f1f8",
    "singapore": "Singapore " + "\U0001f1f8\U0001f1ec",
    "sydney": "Sydney " + "\U0001f1e6\U0001f1fa",
    "brazil": "Brazil " + "\U0001f1e7\U0001f1f7",
    "hongkong": "Hong Kong " + "\U0001f1ed\U0001f1f0",
    "russia": "Russia " + "\U0001f1f7\U0001f1fa",
    "japan": "Japan " + "\U0001f1ef\U0001f1f5",
    "southafrica": "South Africa " + "\U0001f1ff\U0001f1e6",
    "india": "India " + "\U0001f1ee\U0001f1f3",
    "dubai": "Dubai " + "\U0001f1e6\U0001f1ea",
    "south-korea": "South Korea " + "\U0001f1f0\U0001f1f7",
}
VERIF: dict[str, Any] = {"none": "0 - None", "low": "1 - Low", "medium": "2 - Medium", "high": "3 - High", "extreme": "4 - Extreme"}

FEATURES: dict[str, Any] = {
    "ANIMATED_ICON": "Animated Icon",
    "BANNER": "Banner Image",
    "COMMERCE": "Commerce",
    "COMMUNITY": "Community",
    "DISCOVERABLE": "Server Discovery",
    "FEATURABLE": "Featurable",
    "INVITE_SPLASH": "Splash Invite",
    "MEMBER_LIST_DISABLED": "Member list disabled",
    "MEMBER_VERIFICATION_GATE_ENABLED": "Membership Screening enabled",
    "MORE_EMOJI": "More Emojis",
    "NEWS": "News Channels",
    "PARTNERED": "Partnered",
    "PREVIEW_ENABLED": "Preview enabled",
    "PUBLIC_DISABLED": "Public disabled",
    "VANITY_URL": "Vanity URL",
    "VERIFIED": "Verified",
    "VIP_REGIONS": "VIP Voice Servers",
    "WELCOME_SCREEN_ENABLED": "Welcome Screen enabled",
}


class Route(discord.http.Route):
    BASE = "https://discord.com/api/v8"


EMOJI_RE: Pattern[str] = re.compile(r"(<(a)?:[a-zA-Z0-9_]+:([0-9]+)>)")


async def get_twemoji(emoji: str) -> str:
    emoji_unicode = []
    for char in emoji:
        char = hex(ord(char))[2:]
        emoji_unicode.append(char)
    if "200d" not in emoji_unicode:
        emoji_unicode = [c for c in emoji_unicode if c != "fe0f"]
    emoji_unicode = "-".join(emoji_unicode)
    return f"{TWEMOJI_URL}/{emoji_unicode}.png"


def bool_emojify(bool_var: bool) -> str:
    return "✅" if bool_var else "❌"


def category_format(cat_chan_tuple: tuple) -> str:
    cat = cat_chan_tuple[0]
    chs = cat_chan_tuple[1]

    chfs = channels_format(chs)
    if chfs == []:
        return "\n".join([f"{cat.name} :: {cat.id}"] + ["\tNo Channels"])

    ch_forms = ["\t" + f for f in chfs]
    return "\n".join([f"{cat.name} :: {cat.id}", *ch_forms])


def process_avatar_img(url: str, is_animated: bool) -> BytesIO:
    content = worker_download(url)
    i = Image.open(BytesIO(content))
    final = BytesIO()
    if is_animated:
        filename = "MelanieRenderedImg.gif"
        final.name = filename
        i.save(final, format="GIF", optimize=True, save_all=True, loop=0)

    else:
        filename = "MelanieRenderedImg.png"
        final.name = filename
        i.save(final, format="PNG", optimize=True)
    final.seek(0)
    return final


async def is_allowed_by_hierarchy(bot: Melanie, config: Config, guild: discord.Guild, mod: discord.Member, user: discord.Member):
    if not await config.guild(guild).respect_hierarchy():
        return True
    is_special = mod == guild.owner or await bot.is_owner(mod)
    return mod.top_role > user.top_role or is_special


# credits to https://stackoverflow.com/questions/14088375/how-can-i-convert-rgb-to-cmyk-and-vice-versa-in-python
def rgb_to_cmyk(r, g, b):
    rgb_scale = 255
    cmyk_scale = 100
    if (r == 0) and (g == 0) and (b == 0):
        # black
        return 0, 0, 0, cmyk_scale

    # rgb [0,255] -> cmy [0,1]
    c = 1 - (r / float(rgb_scale))
    m = 1 - (g / float(rgb_scale))
    y = 1 - (b / float(rgb_scale))

    # extract out k [0,1]
    min_cmy = min(c, m, y)
    c = (c - min_cmy) / (1 - min_cmy)
    m = (m - min_cmy) / (1 - min_cmy)
    y = (y - min_cmy) / (1 - min_cmy)
    k = min_cmy

    return c * cmyk_scale, m * cmyk_scale, y * cmyk_scale, k * cmyk_scale


def rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0

    cmax = max(r, g, b)
    cmin = min(r, g, b)
    diff = cmax - cmin

    # if cmax and cmax are equal then h = 0
    if cmax == cmin:
        h = 0

    # if cmax equal r then compute h
    elif cmax == r:
        h = (60 * ((g - b) / diff) + 360) % 360

    # if cmax equal g then compute h
    elif cmax == g:
        h = (60 * ((b - r) / diff) + 120) % 360

    # if cmax equal b then compute h
    elif cmax == b:
        h = (60 * ((r - g) / diff) + 240) % 360

    # if cmax equal zero
    s = 0 if cmax == 0 else (diff / cmax) * 100
    # compute v
    v = cmax * 100
    return h, s, v


async def find_app_by_name(where: list, name: str):
    async for item in AsyncIter(where):
        for v in item.values():
            if v == name:
                return item


def channels_format(channels: list):
    if not channels:
        return []

    channel_form = "{name} :: {ctype} :: {cid}"

    def type_name(channel):
        return channel.__class__.__name__[:-7]

    name_justify = max(len(c.name[:24]) for c in channels)
    type_justify = max(len(type_name(c)) for c in channels)

    return [channel_form.format(name=c.name[:24].ljust(name_justify), ctype=type_name(c).ljust(type_justify), cid=c.id) for c in channels]


def sort_channels(channels):
    temp = {}

    channels = sorted(channels, key=lambda c: c.position)

    for c in channels[:]:
        if isinstance(c, discord.CategoryChannel):
            channels.pop(channels.index(c))
            temp[c] = []

    for c in channels[:]:
        if c.category:
            channels.pop(channels.index(c))
            temp[c.category].append(c)

    category_channels = sorted([(cat, sorted(chans, key=lambda c: c.position)) for cat, chans in temp.items()], key=lambda t: t[0].position)
    return channels, category_channels


def dynamic_time(time: str) -> str:
    try:
        date_join = datetime.datetime.strptime(str(time), "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        time = f"{time}.0"
        date_join = datetime.datetime.strptime(str(time), "%Y-%m-%d %H:%M:%S.%f")
    date_now = datetime.datetime.now(datetime.timezone.utc)
    date_now = date_now.replace(tzinfo=None)
    since_join = date_now - date_join

    mins, secs = divmod(int(since_join.total_seconds()), 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    mths, wks, days = count_months(days)
    yrs, mths = divmod(mths, 12)

    m = f"{yrs}y {mths}mth {wks}w {days}d {hrs}h {mins}m {secs}s"
    m2 = [x for x in m.split() if x[0] != "0"]
    s = " ".join(m2[:2])
    return f"{s} ago" if s else ""


def count_months(days: int) -> tuple[int, ...]:
    lens = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    cy = itertools.cycle(lens)
    months = 0
    m_temp = 0
    mo_len = next(cy)
    for _ in range(1, days + 1):
        m_temp += 1
        if m_temp == mo_len:
            months += 1
            m_temp = 0
            mo_len = next(cy)
            if mo_len == 28 and months >= 48:
                mo_len += 1

    weeks, days = divmod(m_temp, 7)
    return months, weeks, days
