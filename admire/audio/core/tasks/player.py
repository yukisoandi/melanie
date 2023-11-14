from __future__ import annotations

import asyncio
import time

import lavalink
from melaniebot.core.utils import AsyncIter

from audio.audio_logging import debug_exc_log
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass
from melanie import log


def _(x):
    return x


class PlayerTasks(MixinMeta, metaclass=CompositeMetaClass):
    async def player_automated_timer(self) -> None:
        stop_times: dict = {}
        pause_times: dict = {}
        while True:
            async for p in AsyncIter(lavalink.all_players()):
                server = p.guild
                if await self.bot.cog_disabled_in_guild(self, server):
                    continue

                if p.channel.members and all(m.bot for m in p.channel.members):
                    stop_times.setdefault(server.id, time.time())
                    pause_times.setdefault(server.id, time.time())
                else:
                    stop_times.pop(server.id, None)
                    if p.paused and server.id in pause_times:
                        try:
                            await p.pause(False)
                        except Exception as err:
                            debug_exc_log(log, err, "Exception raised in Audio's unpausing %r.", p)
                    pause_times.pop(server.id, None)
            servers = stop_times | pause_times
            async for sid in AsyncIter(servers, steps=5):
                server_obj = self.bot.get_guild(sid)
                if not server_obj:
                    stop_times.pop(sid, None)
                    pause_times.pop(sid, None)
                    try:
                        player = lavalink.get_player(sid)
                        await self.api_interface.persistent_queue_api.drop(sid)
                        player.store("autoplay_notified", False)
                        await player.stop()
                        await player.disconnect()
                        await self.config.guild_from_id(guild_id=sid).currently_auto_playing_in.set([])
                    except Exception as err:
                        debug_exc_log(log, err, "Exception raised in Audio's emptydc_timer for {}.", sid)

                elif sid in stop_times and await self.config.guild(server_obj).emptydc_enabled():
                    emptydc_timer = await self.config.guild(server_obj).emptydc_timer()
                    if (time.time() - stop_times[sid]) >= emptydc_timer:
                        stop_times.pop(sid)
                        try:
                            player = lavalink.get_player(sid)
                            await self.api_interface.persistent_queue_api.drop(sid)
                            player.store("autoplay_notified", False)
                            await player.stop()
                            await player.disconnect()
                            await self.config.guild_from_id(guild_id=sid).currently_auto_playing_in.set([])
                        except Exception as err:
                            if "No such player for that guild" in str(err):
                                stop_times.pop(sid, None)
                            debug_exc_log(log, err, "Exception raised in Audio's emptydc_timer for {}.", sid)
                elif sid in pause_times and await self.config.guild(server_obj).emptypause_enabled():
                    emptypause_timer = await self.config.guild(server_obj).emptypause_timer()
                    if (time.time() - pause_times.get(sid, 0)) >= emptypause_timer:
                        try:
                            await lavalink.get_player(sid).pause()
                        except Exception as err:
                            if "No such player for that guild" in str(err):
                                pause_times.pop(sid, None)
                            debug_exc_log(log, err, "Exception raised in Audio's pausing for {}.", sid)
            await asyncio.sleep(5)
