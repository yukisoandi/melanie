from __future__ import annotations

from typing import Any, Optional

import discord
from loguru import logger as log
from melaniebot.core.utils.chat_formatting import humanize_list, pagify
from melaniebot.vendored.discord.ext import menus

from .starboard_entry import StarboardEntry


def _(x):
    return x


class StarboardPages(menus.ListPageSource):
    def __init__(self, roles: list[StarboardEntry]) -> None:
        super().__init__(roles, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, starboard: StarboardEntry) -> discord.Embed:
        guild = menu.ctx.guild
        embed = discord.Embed(colour=await menu.ctx.bot.get_embed_colour(menu.ctx.channel))
        embed.title = f"Starboard settings for {guild.name}"
        channel = guild.get_channel(starboard.channel)
        s_channel = channel.mention if channel else "deleted_channel"
        msg = f"Name: **{starboard.name}**\nEnabled: **{starboard.enabled}**\nEmoji: {starboard.emoji}\nChannel: {s_channel}\nThreshold: **{starboard.threshold}**\n{starboard.emoji} Messages: **{starboard.starred_messages}**\n{starboard.emoji} Added: **{starboard.stars_added}**\nSelfstar: **{starboard.selfstar}**\n"
        if starboard.blacklist:
            channels = [guild.get_channel(c) for c in starboard.blacklist]
            roles = [guild.get_role(r) for r in starboard.blacklist]
            chans = humanize_list([c.mention for c in channels if c is not None])
            roles_str = humanize_list([r.mention for r in roles if r is not None])
            if chans:
                msg += f"Blocked Channels: {chans}\n"
            if roles_str:
                msg += f"Blocked roles: {roles_str}\n"
        if starboard.whitelist:
            channels = [guild.get_channel(c) for c in starboard.whitelist]
            roles = [guild.get_role(r) for r in starboard.whitelist]
            chans = humanize_list([c.mention for c in channels if c is not None])
            roles_str = humanize_list([r.mention for r in roles if r is not None])
            if chans:
                msg += f"Allowed Channels: {chans}\n"
            if roles_str:
                msg += f"Allowed roles: {roles_str}\n"
        count = 0
        embed.description = ""
        for page in pagify(msg, page_length=1024):
            if count <= 1:
                embed.description += msg
            else:
                embed.add_field(name="Starboard info continued", value=page)
        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return embed


class BaseMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        page_start: Optional[int] = 0,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 60,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            source,
            clear_reactions_after=clear_reactions_after,
            delete_message_after=delete_message_after,
            timeout=timeout,
            message=message,
            **kwargs,
        )
        self.page_start = page_start

    async def send_initial_message(self, ctx, channel):
        """|coro| The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.

        """
        self.current_page = self.page_start
        page = await self._source.get_page(self.page_start)
        kwargs = await self._get_kwargs_from_page(page)
        return await channel.send(**kwargs)

    async def update(self, payload) -> None:
        """|coro|.

        Updates the menu after an event has been received.

        Parameters
        ----------
        payload: :class:`discord.RawReactionActionEvent`
            The reaction event that triggered this update.

        """
        button = self.buttons[payload.emoji]
        if not self._running:
            return

        try:
            if button.lock:
                async with self._lock:
                    if self._running:
                        await button(self, payload)
            else:
                await button(self, payload)
        except Exception:
            log.debug("Ignored exception on reaction event")

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None or page_number < max_pages and page_number >= 0:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif page_number >= max_pages:
                await self.show_page(0)
            else:
                await self.show_page(max_pages - 1)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    def reaction_check(self, payload) -> bool:
        """Just extends the default reaction_check to use owner_ids."""
        if payload.message_id != self.message.id:
            return False
        if payload.user_id not in (*self.ctx.bot.owner_ids, self._author_id):
            return False
        return payload.emoji in self.buttons

    def _skip_single_arrows(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages == 1

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages <= 2

    @menus.button("\N{BLACK LEFT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}", position=menus.First(1), skip_if=_skip_single_arrows)
    async def go_to_previous_page(self, payload) -> None:
        """Go to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    @menus.button("\N{BLACK RIGHT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}", position=menus.Last(0), skip_if=_skip_single_arrows)
    async def go_to_next_page(self, payload) -> None:
        """Go to the next page."""
        await self.show_checked_page(self.current_page + 1)

    @menus.button(
        "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}",
        position=menus.First(0),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_first_page(self, payload) -> None:
        """Go to the first page."""
        await self.show_page(0)

    @menus.button(
        "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}",
        position=menus.Last(1),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_last_page(self, payload) -> None:
        """Go to the last page."""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(self._source.get_max_pages() - 1)

    @menus.button("\N{CROSS MARK}")
    async def stop_pages(self, payload: discord.RawReactionActionEvent) -> None:
        """Stops the pagination session."""
        self.stop()
        await self.message.delete()
