from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from contextlib import suppress
from functools import partial
from typing import Optional, Union

import aiopg
import discord
from aiobotocore.session import get_session
from aiomisc import cancel_tasks
from aiomisc.periodic import PeriodicCallback
from anyio import CapacityLimiter
from discord.ext.commands.errors import ExtensionNotLoaded
from loguru import logger as log
from melaniebot.core import Config, checks, commands, modlog
from melaniebot.core import modlog as _modlog
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import humanize_list

from melanie import checkpoint, create_task, get_redis, msgpack
from melanie.core import default_lock_cache
from melanie.helpers import make_e
from melanie.timing import capturetime

from .eventmixin import EventChooser, EventMixin
from .settings import inv_settings


def _(x):
    return x


class ExtendedModLog(EventMixin, commands.Cog):
    """Extended modlogs Works with core modlogset channel."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, 154457677895, force_registration=True)
        self.config.register_guild(**inv_settings)
        self.config.register_global(version="0.0.0")
        self.settings = {}
        self.closed = False
        self.sync_lock = asyncio.Lock()
        self.bulk_submit_tasks = {}
        self.locks = default_lock_cache()
        self.purge_tasks = defaultdict(partial(CapacityLimiter, 1))
        self._ban_cache = {}
        self.count_lock = asyncio.Lock()
        self.bulk_counter = Counter()
        self.single_counter = Counter()
        self.s3_session = get_session()
        self.invite_loop = create_task(self.invite_links_loop())
        self.active_tasks = [create_task(self.check_for_sync())]
        self.checker = PeriodicCallback(self.check_loop)
        self.config.init_custom("WebsiteLogs", 2)
        self.config.register_custom("WebsiteLogs", **{"created_at": None, "size": None})
        self.db: aiopg.Pool = None
        self.redis = get_redis()
        self.checker.start(300)

    async def check_loop(self):
        await self.bot.waits_uptime_for(10)
        await self.settings_sync()

    async def shutdown(self) -> None:
        log.warning("Shutting down Extendedmodlog")
        if self.db:
            self.db.close()
            await self.db.wait_closed()
        log.success("Shutdown done")

    def cog_unload(self) -> None:
        self.closed = True
        self.checker.stop(True)
        self.invite_loop.cancel()
        cancel_tasks(self.bulk_submit_tasks.values())
        cancel_tasks(self.active_tasks)
        create_task(self.shutdown())

    async def check_for_sync(self):
        await self.bot.waits_uptime_for(10)
        pubsub = self.bot.redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe("trigger_modlog_sync")
        try:
            while True:
                with log.catch(exclude=asyncio.CancelledError):
                    await asyncio.sleep(0.2)
                    msg = await pubsub.get_message(ignore_subscribe_messages=True)
                    if self.bot.user.name == "melanie":
                        continue
                    if msg:
                        await self.settings_sync()
        except asyncio.CancelledError:
            await pubsub.unsubscribe("trigger_modlog_sync")
            log.warning("Ended the checker pubsub")
            raise

    async def settings_sync(self):
        if self.bot.user.name == "melanie3":
            with suppress(ExtensionNotLoaded):
                self.bot.unload_extension("extendedmodlog")
            with suppress(ExtensionNotLoaded):
                self.bot.unload_extension("modlog")
            return False
        elif self.bot.user.name == "melanie":
            modlog = Config.get_conf(None, 1354799444, cog_name="ModLog")
            all_guilds = await modlog.all_guilds()
            await self.bot.redis.set("modlog_config_core", msgpack.packb(all_guilds))
            await self.bot.redis.set("modlog_config", msgpack.packb(self.settings))
            await self.bot.redis.publish("trigger_modlog_sync", b"run")
        else:
            if self.sync_lock.locked():
                return
            async with self.sync_lock:
                await asyncio.sleep(0.01)
                with capturetime("sync"):
                    modlog = Config.get_conf(None, 1354799444, cog_name="ModLog")
                    all_guilds = await self.bot.redis.get("modlog_config_core")
                    all_guilds = msgpack.unpackb(all_guilds, strict_map_key=False)
                    for gid, data in all_guilds.items():
                        await checkpoint()
                        async with modlog.guild_from_id(gid).all() as _data:
                            _data.update(data)
                    all_data = await self.bot.redis.get("modlog_config")
                    all_data = msgpack.unpackb(all_data, strict_map_key=False)
                    for gid, data in all_data.items():
                        await checkpoint()
                        async with self.config.guild_from_id(gid).all() as conf:
                            conf.update(data)

                    _modlog._lri_cache.clear()
                    await self.initialize()

    async def initialize(self) -> None:
        all_data = await self.config.all_guilds()
        for guild_id, data in all_data.items():
            guild = discord.Object(id=guild_id)
            for entry, default in inv_settings.items():
                if entry not in data:
                    all_data[guild_id][entry] = inv_settings[entry]
                if isinstance(default, dict):
                    for key, _default in inv_settings[entry].items():
                        if not isinstance(all_data[guild_id][entry], dict):
                            all_data[guild_id][entry] = default
                        try:
                            if key not in all_data[guild_id][entry]:
                                all_data[guild_id][entry][key] = _default
                        except TypeError:
                            log.error("Somehow your dict was invalid.")
                            continue
            if await self.config.version() < "2.8.5":
                log.info("Saving all guild data to new version type")
                await self.config.guild(guild).set(all_data[guild_id])
                await self.config.version.set("2.8.5")

        self.settings = all_data
        await self.settings_sync()

    async def modlog_settings(self, ctx: commands.Context) -> None:
        guild = ctx.message.guild
        try:
            _modlog_channel = await modlog.get_modlog_channel(guild)
            modlog_channel = _modlog_channel.mention
        except asyncio.CancelledError:
            raise
        except Exception:
            modlog_channel = "Not Set"
        cur_settings = {
            "message_edit": "Message edits",
            "message_delete": "Message delete",
            "user_change": "Member changes",
            "role_change": "Role changes",
            "role_create": "Role created",
            "role_delete": "Role deleted",
            "voice_change": "Voice changes",
            "user_join": "Member join",
            "user_left": "Member left",
            "channel_change": "Channel changes",
            "channel_create": "Channel created",
            "channel_delete": "Channel deleted",
            "guild_change": "Guild changes",
            "emoji_change": "Emoji changes",
            "commands_used": "Commands",
            "invite_created": "Invite created",
            "invite_deleted": "Invite deleted",
        }
        msg = f"Setting for {guild.name}\n Modlog Channel {modlog_channel}\n\n"
        if guild.id not in self.settings:
            self.settings[guild.id] = inv_settings

        data = self.settings[guild.id]
        ign_chans = data["ignored_channels"]
        ignored_channels = []
        for c in ign_chans:
            await checkpoint()
            chn = guild.get_channel(c)
            if chn is None:
                # a bit of automatic cleanup so things don't break
                data["ignored_channels"].remove(c)
            else:
                ignored_channels.append(chn)
        enabled = ""
        disabled = ""
        for settings, name in cur_settings.items():
            msg += f"{name}: **{data[settings]['enabled']}**"
            if settings == "commands_used":
                msg += "\n" + humanize_list(data[settings]["privs"])
            if data[settings]["channel"]:
                chn = guild.get_channel(data[settings]["channel"])
                if chn is None:
                    # a bit of automatic cleanup so things don't break
                    data[settings]["channel"] = None
                else:
                    msg += f" {chn.mention}\n"
            else:
                msg += "\n"

        if not enabled:
            enabled = "None  "
        if not disabled:
            disabled = "None  "
        if ignored_channels:
            chans = ", ".join(c.mention for c in ignored_channels)
            msg += "Ignored Channels" + ": " + chans
        await self.config.guild(ctx.guild).set(data)
        # save the data back to config incase we had some deleted channels
        await ctx.maybe_send_embed(msg)

    @checks.admin_or_permissions(manage_channels=True)
    @commands.group(name="modlog", aliases=["modlogtoggle", "modlogs"])
    @commands.guild_only()
    async def _modlog(self, ctx: commands.Context) -> None:
        """Toggle various extended modlog notifications.

        Set the main logging channel with `;;modlog channel <channel>`

        """
        if self.bot.user.name == "melanie2":
            return await ctx.send(embed=make_e("Please use `;modlog` from the primary melanie bot. I will receive the changes here automatically", 3))

    @_modlog.command(name="settings")
    async def _show_modlog_settings(self, ctx: commands.Context) -> None:
        """Show the servers current ExtendedModlog settings."""
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        if await self.config.guild(ctx.message.guild).all() == {}:
            await self.config.guild(ctx.message.guild).set(inv_settings)
        await self.modlog_settings(ctx)

        await self.settings_sync()

    # modlog
    @_modlog.command(name="channel")
    async def _set_main_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the main log channel."""
        await ctx.invoke(ctx.bot.get_command("modlogset modlog"), channel=channel)
        _modlog._lri_cache.clear()
        await self.settings_sync()

    @_modlog.command(name="toggle")
    async def _set_event_on_or_off(self, ctx: commands.Context, true_or_false: bool, *events: EventChooser) -> None:
        """Turn on and off specific modlog actions.

        `<true_or_false>` Either on or off.

        `[events...]` must be any of the following options (more than one event can be provided at once):
            `channel_change` - Updates to channel name, etc.
            `channel_create`
            `channel_delete`
            `commands_used`  - Bot command usage
            `emoji_change`   - Emojis added or deleted
            `guild_change`   - Server settings changed
            `message_edit`
            `message_delete`
            `member_change`  - Member changes like roles added/removed and nicknames
            `role_change`    - Role updates like permissions
            `role_create`
            `role_delete`
            `voice_change`   - Voice channel join/leave
            `member_join`
            `member_left`


            `invite_created`
            `invite_deleted`

        """
        if not events:
            return await ctx.send("You must provide which events should be included.")
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        for event in events:
            self.settings[ctx.guild.id][event]["enabled"] = true_or_false
            await self.config.guild(ctx.guild).set_raw(event, value=self.settings[ctx.guild.id][event])
        await ctx.send(f"{humanize_list([e.replace('user_', 'member_') for e in events])} logs have been set to {true_or_false}")

        await self.settings_sync()

    @_modlog.command(name="setchannel")
    async def _set_event_channel(self, ctx: commands.Context, channel: discord.TextChannel, *events: EventChooser) -> None:
        """Set the channel for modlogs.

                `<channel>` The text channel to send the events to.

                `[events...]` must be any of the following options (more than one event can be provided at once):
                    `channel_change` - Updates to channel name, etc.
                    `channel_create`
                    `channel_delete`
                    `commands_used`  - Bot command usage
                    `emoji_change`   - Emojis added or deleted
                    `guild_change`   - Server settings changed
                    `message_edit`
                    `message_delete`
                    `member_change`  - Member changes like roles added/removed and nicknames
                    `role_change`    - Role updates like permissions
                    `role_create`
                    `role_delete`
                    `voice_change`   - Voice channel join/leave
                    `member_join`
                    `member_left`

        a
                    `invite_created`
                    `invite_deleted`

        """
        if not events:
            return await ctx.send("You must provide which events should be included.")
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        for event in events:
            self.settings[ctx.guild.id][event]["channel"] = channel.id
            await self.config.guild(ctx.guild).set_raw(event, value=self.settings[ctx.guild.id][event])
        await ctx.send(f"{humanize_list([e.replace('user_', 'member_') for e in events])} logs have been set to {channel.mention}")

        await self.settings_sync()

    @_modlog.command(name="resetchannel")
    async def _reset_event_channel(self, ctx: commands.Context, *events: EventChooser) -> None:
        """Reset the modlog event to the default modlog channel.

        `[events...]` must be any of the following options (more than one event can be provided at once):
            `channel_change` - Updates to channel name, etc.
            `channel_create`
            `channel_delete`
            `commands_used`  - Bot command usage
            `emoji_change`   - Emojis added or deleted
            `guild_change`   - Server settings changed
            `message_edit`
            `message_delete`
            `member_change`  - Member changes like roles added/removed and nicknames
            `role_change`    - Role updates like permissions
            `role_create`
            `role_delete`
            `voice_change`   - Voice channel join/leave
            `member_join`
            `member_left`


            `invite_created`
            `invite_deleted`

        """
        if not events:
            return await ctx.send("You must provide which events should be included.")
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        for event in events:
            self.settings[ctx.guild.id][event]["channel"] = None
            await self.config.guild(ctx.guild).set_raw(event, value=self.settings[ctx.guild.id][event])
        await ctx.send(f"{humanize_list(events)} logs channel have been reset.")
        await self.settings_sync()

    @_modlog.command(name="all", aliaes=["all_settings", "toggle_all"])
    async def _toggle_all_logs(self, ctx: commands.Context, true_or_false: Optional[bool]) -> None:
        """Turn all logging options on or off.

        `<true_or_false>` what to set all logging settings to must be
        `true`, `false`, `yes`, `no`.

        """
        async with ctx.typing():
            if ctx.guild.id not in self.settings:
                self.settings[ctx.guild.id] = inv_settings

            if not true_or_false:
                true_or_false = not bool(self.settings[ctx.guild.id]["message_delete"]["enabled"])
            for setting in inv_settings:
                if "enabled" in self.settings[ctx.guild.id][setting]:
                    self.settings[ctx.guild.id][setting]["enabled"] = true_or_false
            await self.config.guild(ctx.guild).set(self.settings[ctx.guild.id])

        await self.modlog_settings(ctx)
        await self.settings_sync()

    @_modlog.group(name="delete")
    async def _delete(self, ctx: commands.Context) -> None:
        """Delete logging settings."""

    @_delete.command(name="bulkdelete")
    async def _delete_bulk_toggle(self, ctx: commands.Context) -> None:
        """Toggle bulk message delete notifications."""
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        guild = ctx.message.guild
        if not await self.config.guild(guild).message_delete.bulk_enabled():
            await self.config.guild(guild).message_delete.bulk_enabled.set(True)
            self.settings[ctx.guild.id]["message_delete"]["bulk_enabled"] = True
            verb = "enabled"
        else:
            await self.config.guild(guild).message_delete.bulk_enabled.set(False)
            self.settings[ctx.guild.id]["message_delete"]["bulk_enabled"] = False
            verb = "disabled"
        await ctx.send(f"Bulk message delete logs {verb}")

    @_modlog.command(name="nickname", aliases=["nicknames"])
    async def _user_nickname_logging(self, ctx: commands.Context) -> None:
        """Toggle nickname updates for user changes."""
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        setting = self.settings[ctx.guild.id]["user_change"]["nicknames"]
        self.settings[ctx.guild.id]["user_change"]["nicknames"] = not setting
        await self.config.guild(ctx.guild).user_change.nicknames.set(not setting)
        if setting:
            await ctx.send("Nicknames will no longer be tracked in user change logs.")
        else:
            await ctx.send("Nicknames will be tracked in user change logs.")

        await self.settings_sync()

    @_modlog.command()
    async def ignore(self, ctx: commands.Context, channel: Union[discord.TextChannel, discord.CategoryChannel, discord.VoiceChannel]) -> None:
        """Ignore a channel from message delete/edit events and bot commands.

        `channel` the channel or category to ignore events in

        """
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        guild = ctx.message.guild
        if channel is None:
            channel = ctx.channel
        cur_ignored = await self.config.guild(guild).ignored_channels()
        if channel.id not in cur_ignored:
            cur_ignored.append(channel.id)
            await self.config.guild(guild).ignored_channels.set(cur_ignored)
            self.settings[guild.id]["ignored_channels"] = cur_ignored
            await ctx.send(f" Now ignoring events in {channel.mention}")
        else:
            await ctx.send(f"{channel.mention} is already being ignored.")
        await self.settings_sync()

    @_modlog.command()
    async def unignore(self, ctx: commands.Context, channel: Union[discord.TextChannel, discord.CategoryChannel, discord.VoiceChannel]) -> None:
        """Unignore a channel from message delete/edit events and bot commands.

        `channel` the channel to unignore message delete/edit events

        """
        if ctx.guild.id not in self.settings:
            self.settings[ctx.guild.id] = inv_settings
        guild = ctx.message.guild
        if channel is None:
            channel = ctx.channel
        cur_ignored = await self.config.guild(guild).ignored_channels()
        if channel.id in cur_ignored:
            cur_ignored.remove(channel.id)
            await self.config.guild(guild).ignored_channels.set(cur_ignored)
            self.settings[guild.id]["ignored_channels"] = cur_ignored
            await ctx.send(f" Now tracking events in {channel.mention}")
        else:
            await ctx.send(f"{channel.mention} is not being ignored.")
        await self.settings_sync()
