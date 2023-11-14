import asyncio
import contextlib
from typing import Any, Optional

import discord
from discord.ext.commands.errors import BadArgument
from loguru import logger as log
from melaniebot.core.commands import commands
from melaniebot.core.utils.chat_formatting import box, humanize_list, pagify
from melaniebot.core.utils.menus import start_adding_reactions
from melaniebot.core.utils.predicates import ReactionPredicate
from melaniebot.vendored.discord.ext import menus

from .converters import ChannelUserRole, Trigger


class ExplainReTriggerPages(menus.ListPageSource):
    def __init__(self, pages: list) -> None:
        super().__init__(pages, per_page=1)
        self.pages = pages

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, page):
        if not menu.ctx.channel.permissions_for(menu.ctx.me).embed_links:
            return page

        em = discord.Embed(description=page, colour=await menu.ctx.bot.get_embed_colour(menu.ctx))
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return em


class ReTriggerPages(menus.ListPageSource):
    def __init__(self, triggers: list[Trigger], guild: discord.Guild) -> None:
        super().__init__(triggers, per_page=1)
        self.active_triggers = triggers
        self.selection = None
        self.guild = guild

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, menu: menus.MenuPages, trigger: Trigger):
        self.selection = trigger
        embeds = menu.ctx.channel.permissions_for(menu.ctx.me).embed_links
        good = "\N{WHITE HEAVY CHECK MARK}"
        bad = "\N{NEGATIVE SQUARED CROSS MARK}"
        author = self.guild.get_member(trigger.author)
        if not author:
            try:
                author = await menu.ctx.bot.fetch_user(trigger.author)
            except asyncio.CancelledError:
                raise
            except Exception:
                author = discord.Object(id=trigger.author)
                author.name = "Unknown or Deleted User"
                author.mention = "Unknown or Deleted User"
                author.avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"
        blacklist = []
        for y in trigger.blacklist:
            try:
                blacklist.append(await ChannelUserRole().convert(menu.ctx, str(y)))
            except BadArgument:
                continue
        blacklist_s = ", ".join(x.mention for x in blacklist) if embeds else ", ".join(x.name for x in blacklist)
        whitelist = []
        for y in trigger.whitelist:
            try:
                whitelist.append(await ChannelUserRole().convert(menu.ctx, str(y)))
            except BadArgument:
                continue
        whitelist_s = ", ".join(x.mention for x in whitelist) if embeds else ", ".join(x.name for x in whitelist)
        responses = humanize_list(trigger.response_type) if trigger.response_type else "This trigger has no actions and should be removed."

        info = "__Name__: **{name}** \n__Active__: **{enabled}**\n"
        if embeds:
            info = info.format(name=trigger.name, enabled=good if trigger.enabled else bad, author=author.mention, count=trigger.count, response=responses)
        else:
            info = info.format(name=trigger.name, enabled=good if trigger.enabled else bad, author=author.name, count=trigger.count, response=responses)
        text_response = ""
        if trigger.ignore_commands:
            info += f"__Ignore commands__: **{trigger.ignore_commands}**\n"
        if "text" in trigger.response_type:
            text_response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "text") if trigger.multi_payload else trigger.text
            if len(text_response) < 200:
                info += f"__Text__: **{text_response}**\n"
        if trigger.reply is not None:
            info += f"__Replies with Notification__:**{trigger.reply}**\n"
        if "rename" in trigger.response_type:
            response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "text") if trigger.multi_payload else trigger.text
            info += f"__Rename__: **{response}**\n"
        if "dm" in trigger.response_type:
            response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "dm") if trigger.multi_payload else trigger.text
            info += f"__DM__: **{response}**\n"
        if "command" in trigger.response_type:
            response = "\n".join(t[1] for t in trigger.multi_payload if t[0] == "command") if trigger.multi_payload else trigger.text
            info += f"__Command__: **{response}**\n"
        if "react" in trigger.response_type:
            emoji_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "react"] if trigger.multi_payload else trigger.text
            server_emojis = "".join(f"<{e}>" for e in emoji_response if len(e) > 5)
            unicode_emojis = "".join(e for e in emoji_response if len(e) < 5)
            info += f"__Emojis__: {server_emojis}{unicode_emojis}" + "\n"
        if "add_role" in trigger.response_type:
            role_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "add_role"] if trigger.multi_payload else trigger.text
            roles = [menu.ctx.guild.get_role(r) for r in role_response]
            roles_list = [r.mention for r in roles if r is not None] if embeds else [r.name for r in roles if r is not None]
            if roles_list:
                info += f"__Roles Added__: {humanize_list(roles_list)}" + "\n"
            else:
                info += "Roles Added: Deleted Roles\n"
        if "remove_role" in trigger.response_type:
            role_response = [r for t in trigger.multi_payload for r in t[1:] if t[0] == "remove_role"] if trigger.multi_payload else trigger.text
            roles = [menu.ctx.guild.get_role(r) for r in role_response]
            roles_list = [r.mention for r in roles if r is not None] if embeds else [r.name for r in roles if r is not None]
            if roles_list:
                info += f"__Roles Removed__: {humanize_list(roles_list)}" + "\n"
            else:
                info += "__Roles Added__: Deleted Roles\n"
        if whitelist_s:
            info += f"__Allowlist__: {whitelist_s}" + "\n"
        if blacklist_s:
            info += f"__Blocklist__: {blacklist_s}" + "\n"
        if trigger.cooldown:
            time = trigger.cooldown["time"]
            style = trigger.cooldown["style"]
            info += f"__Cooldown__: **{time}s per {style}**\n"
        if trigger.ocr_search:
            info += "__OCR__: **Enabled**\n"
        if trigger.check_edits:
            info += "__Checking edits__: **Enabled**\n"
        if trigger.delete_after:
            info += f"__Message deleted after__: {trigger.delete_after} seconds.\n"
        if trigger.read_filenames:
            info += "__Read filenames__: **Enabled**\n"
        if trigger.user_mention:
            info += "__User Mentions__: **Enabled**\n"
        if trigger.everyone_mention:
            info += "__Everyone Mentions__: **Enabled**\n"
        if trigger.role_mention:
            info += "__Role Mentions__: **Enabled**\n"
        if trigger.tts:
            info += "__TTS__: **Enabled**\n"
        if trigger.chance:
            info += f"__Chance__: **1 in {trigger.chance}**\n"
        if embeds:
            em = discord.Embed(timestamp=menu.ctx.message.created_at, colour=await menu.ctx.embed_colour(), title=f"Triggers for {self.guild.name}")
            em.set_author(name=author, icon_url=author.avatar_url)
            if trigger.created_at == 0:
                em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
            else:
                em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()} Created")
                em.timestamp = discord.utils.snowflake_time(trigger.created_at)

            first = True
            for pages in pagify(info, page_length=1024):
                if first:
                    em.description = pages
                    first = False
                else:
                    em.add_field(name="Trigger info continued", value=pages)
            if len(text_response) >= 200:
                use_box = False
                for page in pagify(text_response, page_length=1000):
                    if page.startswith("```"):
                        use_box = True
                    if use_box:
                        em.add_field(name="__Text__", value=box(page.replace("```", ""), lang="text"))
                    else:
                        em.add_field(name="__Text__", value=page)
            for page in pagify(trigger.regex.pattern, page_length=1000):
                em.add_field(name="__Regex__", value=box(page, lang="bf"))
            [em]
        else:
            info += "Regex: " + box(trigger.regex.pattern[: 2000 - len(info)], lang="bf")
        return em if embeds else info


class ReTriggerMenu(menus.MenuPages, inherit_buttons=False):
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
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Ignored exception on reaction event")

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

    @menus.button("\N{BLACK RIGHT-POINTING TRIANGLE WITH DOUBLE VERTICAL BAR}\N{VARIATION SELECTOR-16}")
    async def toggle_trigger(self, payload: discord.RawReactionActionEvent) -> None:
        """Enables and disables triggers."""
        member = self.ctx.guild.get_member(payload.user_id)
        if await self.cog.can_edit(member, self.source.selection):
            self.source.selection.toggle()
            await self.show_checked_page(self.current_page)

    @menus.button("\N{NEGATIVE SQUARED CROSS MARK}")
    async def stop_trigger(self, payload: discord.RawReactionActionEvent) -> None:
        """Enables and disables triggers."""
        member = self.ctx.guild.get_member(payload.user_id)
        if await self.cog.can_edit(member, self.source.selection):
            self.source.selection.disable()
            await self.show_checked_page(self.current_page)

    @menus.button("\N{WHITE HEAVY CHECK MARK}")
    async def enable_trigger(self, payload: discord.RawReactionActionEvent) -> None:
        """Enables and disables triggers."""
        member = self.ctx.guild.get_member(payload.user_id)
        if await self.cog.can_edit(member, self.source.selection):
            self.source.selection.enable()
            await self.show_checked_page(self.current_page)

    @menus.button("\N{PUT LITTER IN ITS PLACE SYMBOL}")
    async def delete_trigger(self, payload: discord.RawReactionActionEvent) -> None:
        """Enables and disables triggers."""
        member = self.ctx.guild.get_member(payload.user_id)
        if await self.cog.can_edit(member, self.source.selection):
            msg = await self.ctx.send(f"Are you sure you want to delete trigger {self.source.selection.name}?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, self.ctx.author)
            await self.ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result:
                await msg.delete()
                self.source.selection.disable()
                done = await self.cog.remove_trigger(payload.guild_id, self.source.selection.name)
                if done:
                    page = await self._source.get_page(self.current_page)
                    kwargs = await self._get_kwargs_from_page(page)
                    await self.message.edit(content="This trigger has been deleted.", embed=kwargs["embed"])
                    for t in self.cog.triggers[self.ctx.guild.id]:
                        if t.name == self.source.selection.name:
                            self.cog.triggers[self.ctx.guild.id].remove(t)


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
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Ignored exception on reaction event")

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
