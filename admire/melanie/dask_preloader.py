from __future__ import annotations

from importlib import import_module

import pyvips
from loguru import logger as log
from PIL import ImageFile

from runtimeopt.preload_logger import build_logger


def run_imports():
    log.warning(pyvips.API_mode)
    import wand.image

    log.warning(wand.image.Image)
    cogs = [
        "melanie",
        "discord",
        "melanie",
        "melaniebot",
        # "alias",
        "anisearch",
        "antinuke",
        "application",
        "audio",
        "away",
        "baron",
        "botstatus",
        "categoryhelp",
        "channelstats",
        "chatgpt",
        "check",
        "colorme",
        "compliment",
        "conversions",
        "customhelp",
        "dankmemer",
        "dashboard",
        "dbump",
        "dictionary",
        # "downloader",
        "embedutils",
        "emojitools",
        "executionstracker",
        "extendedmodlog",
        "fenrir",
        "filter",
        "fixedfloat",
        "fun",
        "gallery",
        "giveaways",
        # "grammar",
        "image",
        "instagram",
        "inviteblocklist",
        "jail",
        "jsk",
        "linkquoter",
        "lock",
        "melutils",
        # "mod",
        "modlog",
        "modsystem",
        "modtoolkit",
        "nicknamer",
        "nickworker",
        "nitrorole",
        # "onedit",
        "partygames",
        # "permissions",
        "phenutils",
        "reactpoll",
        "retrigger",
        "roleplay",
        "roletools",
        "roleutils",
        "savepic",
        "say",
        "seen",
        "serverstats",
        "shutup",
        "smartreact",
        "snipe",
        "spotify",
        "starboard",
        "sticky",
        "tarot",
        "tiktok",
        "translate",
        "twitter",
        "userinfo",
        "vanity",
        "vanitysniper",
        "vanityworker",
        "videofetch",
        "voicemaster",
        "warden",
        "yt_dlp",
        "weather",
        "webhook",
        "welc",
        "welcome",
        "gi.repository.GLib",
        "pyvips",
        "PIL",
        "wand.image.Image",
        "melanie.core",
        "dateparser.search.search_dates",
        "melaniebot.core.bot",
        "melanie.models.colors",
        "melanie.models.base",
        "notsobot.helpers",
        "melanie.models.colors.build_palettes_3",
    ]

    ImageFile.LOAD_TRUNCATED_IMAGES = True

    for c in cogs:
        try:
            if "." in c:
                continue
            import_module(c)

        except Exception as e:
            log.warning("L {}", e)


try:
    L  # type:ignore

except NameError:
    L = 1
    build_logger()
    run_imports()
    log.disable("melanie.models.colors")
