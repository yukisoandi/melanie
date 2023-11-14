from __future__ import annotations

import asyncio  # noqa noqa
import os
from asyncio import AbstractEventLoop  # noqa

import discord  # noqa
from discord import (  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa
    AllowedMentions,
    Asset,
    AuditLogChanges,
    AuditLogDiff,
    AuditLogEntry,
    AutoShardedClient,
    CallMessage,
    Client,
    ClientUser,
    Color,
    Colour,
    Embed,
    Emoji,
    File,
    GroupCall,
    Guild,
    Invite,
    Member,
    PartialEmoji,
    PartialInviteChannel,
    PartialInviteGuild,
    PermissionOverwrite,
    Permissions,
    Profile,
    Relationship,
    Role,
    RoleTags,
    ShardInfo,
    Sticker,
    User,
    VoiceClient,
    VoiceProtocol,
    VoiceState,
    Widget,
    WidgetChannel,
    WidgetMember,
)
from discord.errors import (  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa  # noqa
    ClientException,
    ConnectionClosed,
    DiscordException,
    DiscordServerError,
    Forbidden,
    GatewayNotFound,
    HTTPException,
    InvalidArgument,
    InvalidData,
    LoginFailure,
    NoMoreItems,
    NotFound,
    PrivilegedIntentsRequired,
)
from discord.http import Route  # noqa
from loguru import logger as log  # noqa# noqa
from melaniebot.core import Config
from melaniebot.core.bot import *  # noqa
from melaniebot.core.commands import Context  # noqa
from melaniebot.core.config import Config  # noqa

from melanie.vendor.disputils import BotConfirmation  # noqa

DASK_SCHEDULER_URL: str = os.environ["DASK_HOST"]
REDIS_URL: str = os.environ["REDIS_URL"]
DB_URL: str = os.environ["MELANIE_DB_URL"]
