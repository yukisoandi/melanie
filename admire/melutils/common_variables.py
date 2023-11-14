from __future__ import annotations

import string
from typing import Any

import discord

NON_ESCAPABLE_CHARACTERS: str = string.ascii_letters + string.digits
TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72"
APP_ICON_URL = "https://cdn.discordapp.com/app-icons/{app_id}/{icon_hash}.png"


def _(s):
    return s


GUILD_FEATURES: dict[str, Any] = {
    "VIP_REGIONS": "384kbps voice bitrate",
    "VANITY_URL": "Vanity invite URL",
    "INVITE_SPLASH": "Invite splash{splash}",
    "VERIFIED": "Verified",
    "PARTNERED": "Discord Partner",
    "MORE_EMOJI": "Extended emoji limit",  # Non-boosted?
    "DISCOVERABLE": "Shows in Server Discovery{discovery}",
    "FEATURABLE": _('Can be in "Featured" section of Server Discovery'),
    "COMMERCE": "Store channels",
    "NEWS": "News channels",
    "BANNER": "Banner{banner}",
    "ANIMATED_ICON": "Animated icon",
    "WELCOME_SCREEN_ENABLED": "Welcome screen",
    "PUBLIC_DISABLED": "Cannot be public",
    "ENABLED_DISCOVERABLE_BEFORE": "Was in Server Discovery",
    "COMMUNITY": "Community server",
    "TICKETED_EVENTS_ENABLED": "Ticketed events",
    "MONETIZATION_ENABLED": "Monetization",
    "MORE_STICKERS": "Extended custom sticker slots",
    "THREADS_ENABLED": "Threads",
    "THREADS_ENABLED_TESTING": "Threads (testing)",
    "PRIVATE_THREADS": "Private threads",  # "keep Discord`s core features free"
    "THREE_DAY_THREAD_ARCHIVE": "3 day thread archive",
    "SEVEN_DAY_THREAD_ARCHIVE": "7 day thread archive",
    # Docs from https://github.com/vDelite/DiscordLists:
    "PREVIEW_ENABLED": _('Preview enabled ("Lurkable")'),
    "MEMBER_VERIFICATION_GATE_ENABLED": "Member verification gate enabled",
    "MEMBER_LIST_DISABLED": "Member list disabled",
    # im honestly idk what the fuck that shit means, and discord doesnt provides much docs,
    # so if you see that on your server while using my cog - idk what the fuck is that and how it got there,
    # ask discord to write fucking docs already
    "RELAY_ENABLED": "Shards connections to the guild to different nodes that relay information between each other.",
    "FORCE_RELAY": "Shards connections to the guild to different nodes that relay information between each other.",
}

ACTIVITY_TYPES = {
    discord.ActivityType.playing: "Playing",
    discord.ActivityType.watching: "Watching",
    discord.ActivityType.listening: "Listening to",
    discord.ActivityType.competing: "Competing in",
}

CHANNEL_TYPE_EMOJIS = {
    discord.ChannelType.text: "\N{SPEECH BALLOON}",
    discord.ChannelType.voice: "\N{SPEAKER}",
    discord.ChannelType.category: "\N{BOOKMARK TABS}",
    discord.ChannelType.news: "\N{NEWSPAPER}",
    discord.ChannelType.store: "\N{SHOPPING TROLLEY}",
    discord.ChannelType.private: "\N{BUST IN SILHOUETTE}",
    discord.ChannelType.group: "\N{BUSTS IN SILHOUETTE}",
    discord.ChannelType.stage_voice: "\N{SATELLITE ANTENNA}",
}

KNOWN_CHANNEL_TYPES = {
    "category": ("categories", "Categories"),
    "text": ("text_channels", "Text channels"),
    "voice": ("voice_channels", "Voice channels"),
    "stage": ("stage_channels", "Stage channels"),
}  # menu type: (guild attr name, i18n string)
