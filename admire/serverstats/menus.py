from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Optional, Union

import discord
from melaniebot.core import commands
from melaniebot.vendored.discord.ext import menus

from melanie import footer_gif, get_image_colors2


class AvatarPages(menus.ListPageSource):
    def __init__(self, members: list[discord.Member], dask_client) -> None:
        super().__init__(members, per_page=1)
        self.dask_client = dask_client

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, member: discord.Member) -> discord.Embed:
        img_url = str(member.avatar_url).replace(".webp", ".gif") if member.is_avatar_animated() else str(member.avatar_url).replace(".webp", ".png")

        em = discord.Embed(description=f"{member.display_name}'s avatar")

        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(1):
                lookup = await get_image_colors2(img_url)
                if lookup:
                    em.color = lookup.dominant.decimal
        em.set_image(url=img_url)
        em.set_footer(text="melanie ^_^", icon_url=footer_gif)

        return em


class GuildPages(menus.ListPageSource):
    def __init__(self, guilds: list[discord.Guild]) -> None:
        super().__init__(guilds, per_page=1)
        self.guild: Optional[discord.Guild] = None

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, guild: discord.Guild):
        self.guild = guild
        em = await menu.cog.guild_embed(guild)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class ListPages(menus.ListPageSource):
    def __init__(self, pages: list[Union[discord.Embed, str]]) -> None:
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, page: Union[discord.Embed, str]):
        return page


class BaseMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        cog: commands.Cog,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 60,
        message: discord.Message = None,
        page_start: int = 0,
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
        self.cog = cog
        self.page_start = page_start

    async def send_initial_message(self, ctx, channel):
        """|coro| The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.

        """
        page = await self._source.get_page(self.page_start)
        kwargs = await self._get_kwargs_from_page(page)
        return await channel.send(**kwargs)

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        with contextlib.suppress(IndexError):
            if max_pages is None or page_number < max_pages and page_number >= 0:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif page_number >= max_pages:
                await self.show_page(0)
            else:
                await self.show_page(max_pages - 1)

    def reaction_check(self, payload) -> bool:
        """Just extends the default reaction_check to use owner_ids."""
        if payload.message_id != self.message.id:
            return False
        if payload.user_id not in (*self.bot.owner_ids, self._author_id):
            return False
        return payload.emoji in self.buttons

    def _skip_single_arrows(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages == 1

    def _skip_double_triangle_buttons(self):
        max_pages = self._source.get_max_pages()
        return True if max_pages is None else max_pages <= 2

    def _skip_non_guild_buttons(self) -> bool:
        if self.ctx.author.id not in self.bot.owner_ids:
            return True
        return bool(not isinstance(self.source, GuildPages))

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

    @menus.button("\N{OUTBOX TRAY}", skip_if=_skip_non_guild_buttons)
    async def leave_guild_button(self, payload) -> None:
        await self.cog.confirm_leave_guild(self.ctx, self.source.guild)

    @menus.button("\N{INBOX TRAY}", skip_if=_skip_non_guild_buttons)
    async def make_guild_invite_button(self, payload) -> None:
        invite = await self.cog.get_guild_invite(self.source.guild)
        if invite:
            await self.ctx.send(str(invite))
        else:
            await self.ctx.send(f"I cannot find or create an invite for `{self.source.guild.name}`")
