from __future__ import annotations

import string

import discord


def _(x):
    return x


default_global = {
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


NON_ESCAPABLE_CHARACTERS: str = string.ascii_letters + string.digits
TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72"
APP_ICON_URL = "https://cdn.discordapp.com/app-icons/{app_id}/{icon_hash}.png"


async def get_twemoji(emoji: str) -> str:
    emoji_unicode = []
    for char in emoji:
        char = hex(ord(char))[2:]
        emoji_unicode.append(char)
    if "200d" not in emoji_unicode:
        emoji_unicode = [c for c in emoji_unicode if c != "fe0f"]
    emoji_unicode = "-".join(emoji_unicode)
    return f"{TWEMOJI_URL}/{emoji_unicode}.png"


async def activity_string(activity: discord.Activity) -> str:
    # sourcery no-metrics
    """Make embed with info about activity."""
    if isinstance(activity, discord.Activity):
        party_size = activity.party.get("size")
        party_size = f": {party_size[0]}/{party_size[1]})" if party_size else ""
        return f"{activity.details and activity.details or ''}{activity.state and activity.state or ''}{party_size}"
