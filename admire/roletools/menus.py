from __future__ import annotations

import contextlib
from typing import Any, Optional

import discord
from loguru import logger as log
from melaniebot.core import bank
from melaniebot.core.commands import commands
from melaniebot.core.utils.chat_formatting import humanize_list, pagify
from melaniebot.vendored.discord.ext import menus


def _(x):
    return x


class ReactRolePages(menus.ListPageSource):
    def __init__(self, pages: list) -> None:
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, page):
        if not menu.ctx.channel.permissions_for(menu.ctx.me).embed_links:
            return page
        em = discord.Embed(description=page, colour=await menu.ctx.bot.get_embed_colour(menu.ctx))
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class RolePages(menus.ListPageSource):
    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(roles, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, role: discord.Role):
        role_settings = await menu.cog.config.role(role).all()
        msg = _(f"Role Settings for {role.name}\n")
        jump_url = "https://discord.com/channels/{guild}/{channel}/{message}"
        em = discord.Embed(title=msg, colour=role.colour)
        mod_roles = await menu.bot.get_mod_roles(menu.ctx.guild)
        admin_roles = await menu.bot.get_admin_roles(menu.ctx.guild)
        required_roles = [menu.ctx.guild.get_role(i) for i in role_settings["required"]]
        exclusive_roles = [menu.ctx.guild.get_role(i) for i in role_settings["exclusive_to"]]
        inclusive_roles = [menu.ctx.guild.get_role(i) for i in role_settings["inclusive_with"]]
        permissions = humanize_list([perm.replace("_", " ").title() for perm, value in role.permissions if value])
        settings = f"{role.mention}\n```md\n# ID:           {role.id}\nColour          {str(role.colour)}\n# RoleTools settings\nSticky          {role_settings['sticky']}\nAuto            {role_settings['auto']}\nSelfassignable  {role_settings['selfassignable']}\nSelfremovable   {role_settings['selfremovable']}\n# Core Bot settings\nMod             {role in mod_roles}\nAdmin           {role in admin_roles}\n\n```"
        if cost := role_settings.get("cost"):
            currency_name = await bank.get_currency_name(menu.ctx.guild)
            settings += f"**Cost:** {cost} {currency_name}\n"
        if permissions:
            settings += f"**Permissions:** {permissions}\n"
        if role.managed:
            if getattr(role, "is_bot_managed", lambda: False)():
                bot = role.guild.get_member(role.tags.bot_id)
                settings += f"Bot Role: {bot.mention}"
            elif getattr(role, "is_premium_subscriber", lambda: False)():
                settings += "**Premium Role:** True\n"
            else:
                settings += "**Managed Role:** True\n"
        if inclusive_roles:
            settings += f"**Inclusive with:** {humanize_list([r.mention for r in inclusive_roles if r])}\n"
        if exclusive_roles:
            settings += f"**Exclusive to:** {humanize_list([r.mention for r in exclusive_roles if r])}\n"
        if required_roles:
            settings += f"**Requires:** {humanize_list([r.mention for r in required_roles if r])}\n"
        if role_settings["reactions"]:
            settings += "**Reaction Roles**\n"
        for reaction in role_settings["reactions"]:
            channel, message, emoji = reaction.split("-")
            if emoji.isdigit():
                emoji = menu.bot.get_emoji(int(emoji))
            if not emoji:
                emoji = "Emoji from another server"
            link = jump_url.format(guild=menu.ctx.guild.id, channel=channel, message=message)
            settings += f"{emoji} on [message]({link})\n"
        embeds = list(pagify(settings, page_length=5500))
        if len(embeds) > 1:
            command = f"`{menu.ctx.clean_prefix}roletools reactionroles`"
            settings = f"{embeds[0]}\nPlease see {command} to see full details"
        pages = pagify(settings, page_length=1024)
        em.description = ""
        for index, page in enumerate(pages):
            if index < 2:
                em.description += page
            else:
                em.add_field(name=f"Role settings for {role.name} (continued)", value=page)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class BaseMenu(menus.MenuPages, inherit_buttons=False):
    def __init__(
        self,
        source: menus.PageSource,
        cog: Optional[commands.Cog] = None,
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
        self.cog = cog
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
            log.opt(exception=True).debug("Ignored exception on reaction event")

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        with contextlib.suppress(IndexError):
            if max_pages is None or page_number < max_pages and page_number >= 0:
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
