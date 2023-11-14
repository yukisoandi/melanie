from __future__ import annotations

import contextlib
import math
from collections.abc import MutableMapping
from pathlib import Path

import discord
from melaniebot.core import commands
from melaniebot.core.utils.menus import (
    DEFAULT_CONTROLS,
    close_menu,
    menu,
    next_page,
    prev_page,
)

from audio.audio_dataclasses import LocalPath, Query
from audio.core.abc import MixinMeta  # type: ignore
from audio.core.cog_utils import CompositeMetaClass


def _(x):
    return x


class LocalTrackCommands(MixinMeta, metaclass=CompositeMetaClass):
    @commands.group(name="local", hidden=True)
    @commands.guild_only()
    async def command_local(self, ctx: commands.Context) -> None:
        """Local playback commands."""

    @command_local.command(name="folder", aliases=["start"])
    async def command_local_folder(self, ctx: commands.Context, *, folder: str = None):
        """Play all songs in a localtracks folder."""
        if not await self.localtracks_folder_exists(ctx):
            return

        if not folder:
            await ctx.invoke(self.command_local_play)
        else:
            folder = folder.strip()
            _dir = LocalPath.joinpath(self.local_folder_current_path, folder)
            if not _dir.exists():
                return await self.send_embed_msg(
                    ctx,
                    title="Folder Not Found",
                    description=("Localtracks folder named {name} does not exist.").format(name=folder),
                )
            query = Query.process_input(_dir, self.local_folder_current_path, search_subfolders=True)
            await self._local_play_all(ctx, query, from_search=bool(folder))

    @command_local.command(name="play")
    async def command_local_play(self, ctx: commands.Context):
        """Play a local track."""
        if not await self.localtracks_folder_exists(ctx):
            return
        localtracks_folders = await self.get_localtracks_folders(ctx, search_subfolders=True)
        if not localtracks_folders:
            return await self.send_embed_msg(ctx, title="No album folders found.")
        async with ctx.typing():
            len_folder_pages = math.ceil(len(localtracks_folders) / 5)
            folder_page_list = []
            for page_num in range(1, len_folder_pages + 1):
                embed = await self._build_search_page(ctx, localtracks_folders, page_num)
                folder_page_list.append(embed)

        async def _local_folder_menu(
            ctx: commands.Context,
            pages: list,
            controls: MutableMapping,
            message: discord.Message,
            page: int,
            timeout: float,
            emoji: str,
        ):
            if message:
                with contextlib.suppress(discord.HTTPException):
                    await message.delete()
                await self._search_button_action(ctx, localtracks_folders, emoji, page)
                return None

        local_folder_controls = {
            "\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}": _local_folder_menu,
            "\N{DIGIT TWO}\N{COMBINING ENCLOSING KEYCAP}": _local_folder_menu,
            "\N{DIGIT THREE}\N{COMBINING ENCLOSING KEYCAP}": _local_folder_menu,
            "\N{DIGIT FOUR}\N{COMBINING ENCLOSING KEYCAP}": _local_folder_menu,
            "\N{DIGIT FIVE}\N{COMBINING ENCLOSING KEYCAP}": _local_folder_menu,
            "\N{LEFTWARDS BLACK ARROW}\N{VARIATION SELECTOR-16}": prev_page,
            "\N{CROSS MARK}": close_menu,
            "\N{BLACK RIGHTWARDS ARROW}\N{VARIATION SELECTOR-16}": next_page,
        }

        dj_enabled = await self.config.guild(ctx.guild).dj_enabled()
        if dj_enabled and not await self._can_instaskip(ctx, ctx.author):
            return await menu(ctx, folder_page_list, DEFAULT_CONTROLS)
        else:
            await menu(ctx, folder_page_list, local_folder_controls)

    @command_local.command(name="search")
    async def command_local_search(self, ctx: commands.Context, *, search_words):
        """Search for songs across all localtracks folders."""
        if not await self.localtracks_folder_exists(ctx):
            return
        all_tracks = await self.get_localtrack_folder_list(
            ctx,
            (Query.process_input(Path(await self.config.localpath()).absolute(), self.local_folder_current_path, search_subfolders=True)),
        )
        if not all_tracks:
            return await self.send_embed_msg(ctx, title="No album folders found.")
        async with ctx.typing():
            search_list = await self._build_local_search_list(all_tracks, search_words)
        return await ctx.invoke(self.command_search, query=search_list) if search_list else await self.send_embed_msg(ctx, title="No matches.")
