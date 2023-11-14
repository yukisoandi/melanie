from __future__ import annotations

import asyncio
import contextlib
import datetime
import getpass
import importlib
import itertools
import keyword
import os
import platform
import sys
import traceback
from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import discord
import distro
import pip
import psutil
import regex as re
import stackprinter
from babel import Locale as BabelLocale
from babel import UnknownLocaleError
from loguru import logger
from melanie.deepreload import reload_module_dask

from melaniebot.core import data_manager
from melaniebot.core.commands import GuildConverter
from melaniebot.core.data_manager import storage_type
from melaniebot.core.utils.menus import DEFAULT_CONTROLS, menu

from . import __version__, checks, commands, errors, i18n
from . import version_info as red_version_info
from ._diagnoser import IssueDiagnoser
from .commands import CogConverter, CommandConverter
from .commands.requires import PrivilegeLevel
from .utils import AsyncIter
from .utils.chat_formatting import (
    box,
    humanize_list,
    humanize_number,
    humanize_timedelta,
    inline,
    pagify,
)
from .utils.predicates import MessagePredicate

_entities = {
    "*": "&midast;",
    "\\": "&bsol;",
    "`": "&grave;",
    "!": "&excl;",
    "{": "&lcub;",
    "[": "&lsqb;",
    "_": "&UnderBar;",
    "(": "&lpar;",
    "#": "&num;",
    ".": "&period;",
    "+": "&plus;",
    "}": "&rcub;",
    "]": "&rsqb;",
    ")": "&rpar;",
}

PRETTY_HTML_HEAD = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3rd Party Data Statements</title>
<style type="text/css">
body{margin:2em auto;max-width:800px;line-height:1.4;font-size:16px;
background-color=#EEEEEE;color:#454545;padding:1em;text-align:justify}
h1,h2,h3{line-height:1.2}
</style></head><body>
"""  # This ends up being a small bit extra that really makes a difference.

HTML_CLOSING = "</body></html>"


def entity_transformer(statement: str) -> str:
    return "".join(_entities.get(c, c) for c in statement)


def permission_manual_rule(ctx: commands.Context) -> bool:
    return False


if TYPE_CHECKING:
    from melaniebot.core.bot import Melanie

__all__ = ["Core"]


def _(x):
    return x


TokenConverter = commands.get_dict_converter(delims=[" ", ",", ";"])

MAX_PREFIX_LENGTH = 20


class CoreLogic:
    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.bot.register_rpc_handler(self._load)
        self.bot.register_rpc_handler(self._unload)
        self.bot.register_rpc_handler(self._reload)
        self.bot.register_rpc_handler(self._name)
        self.bot.register_rpc_handler(self._prefixes)
        self.bot.register_rpc_handler(self._version_info)
        self.bot.register_rpc_handler(self._invite_url)

    async def _load(self, pkg_names: Iterable[str]) -> tuple[list[str], list[str], list[str], list[str], list[str], list[tuple[str, str]], set[str]]:
        """Loads packages by name.

        Parameters
        ----------
        pkg_names : `list` of `str`
            List of names of packages to load.

        Returns
        -------
        tuple
            7-tuple of:
              1. List of names of packages that loaded successfully
              2. List of names of packages that failed to load without specified reason
              3. List of names of packages that don't have a valid package name
              4. List of names of packages that weren't found in any cog path
              5. List of names of packages that are already loaded
              6. List of 2-tuples (pkg_name, reason) for packages
              that failed to load with a specified reason
              7. Set of repo names that use deprecated shared libraries

        """
        failed_packages = []
        loaded_packages = []
        invalid_pkg_names = []
        notfound_packages = []
        alreadyloaded_packages = []
        failed_with_reason_packages = []
        repos_with_shared_libs = set()

        bot = self.bot

        pkg_specs = []

        for name in pkg_names:
            if not name.isidentifier() or keyword.iskeyword(name):
                invalid_pkg_names.append(name)
                continue
            try:
                spec = await bot._cog_mgr.find_cog(name)
                if spec:
                    pkg_specs.append((spec, name))
                else:
                    notfound_packages.append(name)
            except Exception as e:
                logger.opt(exception=e).error("Package import failed")

                exception_log = "Exception during import of package\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__))

                bot._last_exception = exception_log
                failed_packages.append(name)

        async for spec, name in AsyncIter(pkg_specs, steps=10):
            try:
                self._cleanup_and_refresh_modules(spec.name)
                await bot.load_extension(spec)
            except errors.PackageAlreadyLoaded:
                alreadyloaded_packages.append(name)
            except errors.CogLoadError as e:
                failed_with_reason_packages.append((name, str(e)))
            except Exception as e:
                if isinstance(e, commands.CommandRegistrationError):
                    if e.alias_conflict:
                        error_message = f"Alias {inline(e.name)} is already an existing command or alias in one of the loaded cogs."
                    else:
                        error_message = f"Command {inline(e.name)} is already an existing command or alias in one of the loaded cogs."
                    failed_with_reason_packages.append((name, error_message))
                    continue

                logger.opt(exception=e).exception("Package loading failed")

                exception_log = "Exception during loading of package\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__))

                bot._last_exception = exception_log
                failed_packages.append(name)
            else:
                await bot.add_loaded_package(name)
                loaded_packages.append(name)
                # remove in Melanie 3.4
                downloader = bot.get_cog("Downloader")
                if downloader is None:
                    continue
                try:
                    maybe_repo = await downloader._shared_lib_load_check(name)
                except Exception:
                    logger.exception("Shared library check failed, if you're not using modified Downloader, report this issue.")
                    maybe_repo = None
                if maybe_repo is not None:
                    repos_with_shared_libs.add(maybe_repo.name)

        return (
            loaded_packages,
            failed_packages,
            invalid_pkg_names,
            notfound_packages,
            alreadyloaded_packages,
            failed_with_reason_packages,
            repos_with_shared_libs,
        )

    @staticmethod
    def _cleanup_and_refresh_modules(module_name: str) -> None:
        """Internally reloads modules so that changes are detected."""
        splitted = module_name.split(".")

        def maybe_reload(new_name):
            try:
                lib = sys.modules[new_name]
            except KeyError:
                pass
            else:
                importlib._bootstrap._exec(lib.__spec__, lib)

        # noinspection PyTypeChecker
        modules = itertools.accumulate(splitted, "{}.{}".format)
        for m in modules:
            maybe_reload(m)

        children = {name: lib for name, lib in sys.modules.items() if name == module_name or name.startswith(f"{module_name}.")}
        for lib in children.values():
            importlib._bootstrap._exec(lib.__spec__, lib)

    async def _unload(self, pkg_names: Iterable[str]) -> tuple[list[str], list[str]]:
        """Unloads packages with the given names.

        Parameters
        ----------
        pkg_names : `list` of `str`
            List of names of packages to unload.

        Returns
        -------
        tuple
            2 element tuple of successful unloads and failed unloads.

        """
        failed_packages = []
        unloaded_packages = []

        bot = self.bot

        for name in pkg_names:
            if name in bot.extensions:
                bot.unload_extension(name)
                await bot.remove_loaded_package(name)
                unloaded_packages.append(name)
            else:
                failed_packages.append(name)

        return unloaded_packages, failed_packages

    async def _reload(self, pkg_names: Sequence[str]) -> tuple[list[str], list[str], list[str], list[str], list[str], list[tuple[str, str]], set[str]]:
        """Reloads packages with the given names.

        Parameters
        ----------
        pkg_names : `list` of `str`
            List of names of packages to reload.

        Returns
        -------
        tuple
            Tuple as returned by `CoreLogic._load()`

        """
        await self._unload(pkg_names)

        (loaded, load_failed, invalid_pkg_names, not_found, already_loaded, load_failed_with_reason, repos_with_shared_libs) = await self._load(pkg_names)

        return (loaded, load_failed, invalid_pkg_names, not_found, already_loaded, load_failed_with_reason, repos_with_shared_libs)

    async def _name(self, name: Optional[str] = None) -> str:
        """Gets or sets the bot's username.

        Parameters
        ----------
        name : str
            If passed, the bot will change it's username.

        Returns
        -------
        str
            The current (or new) username of the bot.

        """
        if name is not None:
            await self.bot.user.edit(username=name)

        return self.bot.user.name

    async def _prefixes(self, prefixes: Optional[Sequence[str]] = None) -> list[str]:
        """Gets or sets the bot's global prefixes.

        Parameters
        ----------
        prefixes : list of str
            If passed, the bot will set it's global prefixes.

        Returns
        -------
        list of str
            The current (or new) list of prefixes.

        """
        if prefixes:
            await self.bot.set_prefixes(guild=None, prefixes=prefixes)
            return prefixes
        return await self.bot._prefix_cache.get_prefixes(guild=None)

    @classmethod
    async def _version_info(cls) -> dict[str, str]:
        """Version information for Melanie and discord.py.

        Returns
        -------
        dict
            `melaniebot` and `discordpy` keys containing version information for both.

        """
        return {"melaniebot": __version__, "discordpy": discord.__version__}

    async def _invite_url(self) -> str:
        """Generates the invite URL for the bot.

        Returns
        -------
        str
            Invite URL.

        """
        app_info = await self.bot.application_info()
        data = await self.bot._config.all()
        commands_scope = data["invite_commands_scope"]
        scopes = ("bot", "applications.commands") if commands_scope else None
        perms_int = data["invite_perm"]
        permissions = discord.Permissions(perms_int)
        return discord.utils.oauth_url(app_info.id, permissions, scopes=scopes)

    @staticmethod
    async def _can_get_invite_url(ctx):
        is_owner = await ctx.bot.is_owner(ctx.author)
        is_invite_public = await ctx.bot._config.invite_public()
        return is_owner or is_invite_public


class Core(commands.commands._RuleDropper, commands.Cog, CoreLogic):
    """The Core cog has many commands related to core functions.

    These commands come loaded with every Melanie bot, and cover some of
    the most basic usage of the bot.

    """

    @commands.command(hidden=True)
    async def ping(self, ctx: commands.Context):
        """Pong."""
        await ctx.send("Pong.")

    @checks.is_owner()
    @commands.command(hidden=True)
    async def info(self, ctx: commands.Context):
        """Shows info about [botname].

        See `;set custominfo` to customize.

        """
        embed_links = await ctx.embed_requested()
        author_repo = "https://github.com/Twentysix26"
        org_repo = "https://github.com/Cog-Creators"
        red_repo = f"{org_repo}/"
        red_pypi = "https://pypi.org/project"
        support_server_url = "https://discord.gg/melanie"
        dpy_repo = "https://github.com/Rapptz/discord.py"
        python_url = "https://www.python.org/"
        since = datetime.datetime(2016, 1, 2, 0, 0)
        days_since = (datetime.datetime.now(datetime.timezone.utc) - since).days

        app_info = await self.bot.application_info()
        owner = app_info.team.name if app_info.team else app_info.owner
        custom_info = await self.bot._config.custom_info()

        pypi_version, py_version_req = 999, 999
        outdated = pypi_version and pypi_version > red_version_info

        if embed_links:
            dpy_version = f"[{discord.__version__}]({dpy_repo})"
            python_version = "[{}.{}.{}]({})".format(*sys.version_info[:3], python_url)
            red_version = f"[{__version__}]({red_pypi})"

            about = f"This bot is an instance of [Melanie, an open source Discord bot]({red_repo}) created by [Monty]({author_repo}) and [improved by many]({org_repo}).\nRed is backed by a passionate community who contributes and creates content for everyone to enjoy. [Join us today]({support_server_url}) and help us improve!\n(c) Cog Creators"

            embed = discord.Embed(color=(await ctx.embed_colour()))
            embed.add_field(name="Instance owned by team" if app_info.team else ("Instance owned by"), value=str(owner))
            embed.add_field(name="Python", value=python_version)
            embed.add_field(name="discord.py", value=dpy_version)
            embed.add_field(name="Melanie version", value=red_version)
            if outdated in (True, None):
                if outdated is True:
                    outdated_value = f"Yes, {pypi_version} is available."
                else:
                    outdated_value = "Checking for updates failed."
                embed.add_field(name="Outdated", value=outdated_value)
            if custom_info:
                embed.add_field(name="About this instance", value=custom_info, inline=False)
            embed.add_field(name="About Melanie", value=about, inline=False)

            embed.set_footer(text=f"Bringing joy since 02 Jan 2016 (over {days_since} days ago!)")
            await ctx.send(embed=embed)
        else:
            python_version = "{}.{}.{}".format(*sys.version_info[:3])
            dpy_version = f"{discord.__version__}"
            red_version = f"{__version__}"

            about = "This bot is an instance of Melanie, an open source Discord bot (1) created by Monty (2) and improved by many (3).\nRed is backed by a passionate community who contributes and creates content for everyone to enjoy. Join us today (4) and help us improve!\n(c) Cog Creators"
            about = box(about)

            if app_info.team:
                extras = (
                    "Instance owned by team: [{owner}]\nPython:                 [{python_version}] (5)\ndiscord.py:             [{dpy_version}] (6)\nRed version:            [{red_version}] (7)\n"
                ).format(owner=owner, python_version=python_version, dpy_version=dpy_version, red_version=red_version)
            else:
                extras = (
                    "Instance owned by: [{owner}]\nPython:            [{python_version}] (5)\ndiscord.py:        [{dpy_version}] (6)\nRed version:       [{red_version}] (7)\n"
                ).format(owner=owner, python_version=python_version, dpy_version=dpy_version, red_version=red_version)

            melanie = (
                "**About Melanie**\n" + about + "\n" + box(extras, lang="ini") + "\n" + f"Bringing joy since 02 Jan 2016 (over {days_since} days ago!)" + "\n"
            )

            await ctx.send(melanie)
            if custom_info:
                custom_info = "**About this instance**\n" + custom_info + "\n"
                await ctx.send(custom_info)
            refs = f"**References**\n1. <{red_repo}>\n2. <{author_repo}>\n3. <{org_repo}>\n4. <{support_server_url}>\n5. <{python_url}>\n6. <{dpy_repo}>\n7. <{red_pypi}>\n"
            await ctx.send(refs)

    @checks.is_owner()
    @commands.command(hidden=True)
    async def uptime(self, ctx: commands.Context):
        """Shows [botname]'s uptime."""
        since = ctx.bot.uptime.strftime("%Y-%m-%d %H:%M:%S")
        delta = datetime.datetime.now(datetime.timezone.utc) - self.bot.uptime
        uptime_str = humanize_timedelta(timedelta=delta) or ("Less than one second")
        await ctx.send(f"Been up for: **{uptime_str}** (since {since} UTC)")

    @checks.is_owner()
    @commands.group(hidden=True)
    async def embedset(self, ctx: commands.Context):
        """Commands for toggling embeds on or off.

        This setting determines whether or not to use embeds as a response to a command (for commands that support it).
        The default is to use embeds.

        The embed settings are checked until the first True/False in this order:
            - In guild context:
                1. Channel override - `;embedset channel`
                2. Server command override - `;embedset command server`
                3. Server override - `;embedset server`
                4. Global command override - `;embedset command global`
                5. Global setting  -`;embedset global`

            - In DM context:
                1. User override - `;embedset user`
                2. Global command override - `;embedset command global`
                3. Global setting - `;embedset global`

        """

    @embedset.command(name="showsettings", hidden=True)
    async def embedset_showsettings(self, ctx: commands.Context, command_name: str = None) -> None:
        """Show the current embed settings.

        Provide a command name to check for command specific embed settings.

        **Examples:**
            - `;embedset showsettings` - Shows embed settings.
            - `;embedset showsettings info` - Also shows embed settings for the 'info' command.
            - `;embedset showsettings "ignore list"` - Checking subcommands requires quotes.

        **Arguments:**
            - `[command_name]` - Checks this command for command specific embed settings.

        """
        if command_name is not None:
            command_obj: Optional[commands.Command] = ctx.bot.get_command(command_name)
            if command_obj is None:
                await ctx.send("I couldn't find that command. Please note that it is case sensitive.")
                return
            # qualified name might be different if alias was passed to this command
            command_name = command_obj.qualified_name

        global_default = await self.bot._config.embeds()
        text = "Embed settings:\n\n" + f"Global default: {global_default}\n"
        if command_name is not None:
            scope = self.bot._config.custom("COMMAND", command_name, 0)
            global_command_setting = await scope.embeds()
            text += f"Global command setting for {inline(command_name)} command: {global_command_setting}\n"

        if ctx.guild:
            guild_setting = await self.bot._config.guild(ctx.guild).embeds()
            text += f"Guild setting: {guild_setting}\n"

            if command_name is not None:
                scope = self.bot._config.custom("COMMAND", command_name, ctx.guild.id)
                command_setting = await scope.embeds()
                text += f"Server command setting for {inline(command_name)} command: {command_setting}\n"

        if ctx.channel:
            channel_setting = await self.bot._config.channel(ctx.channel).embeds()
            text += f"Channel setting: {channel_setting}\n"

        user_setting = await self.bot._config.user(ctx.author).embeds()
        text += f"User setting: {user_setting}"
        await ctx.send(box(text))

    @embedset.command(name="global")
    @checks.is_owner()
    async def embedset_global(self, ctx: commands.Context):
        """Toggle the global embed setting.

        This is used as a fallback if the user or guild hasn't set a preference.
        The default is to use embeds.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Example:**
            - `;embedset global`

        """
        current = await self.bot._config.embeds()
        if current:
            await self.bot._config.embeds.set(False)
            await ctx.send("Embeds are now disabled by default.")
        else:
            await self.bot._config.embeds.clear()
            await ctx.send("Embeds are now enabled by default.")

    @embedset.command(name="server", aliases=["guild"])
    @checks.guildowner_or_permissions(administrator=True)
    @commands.guild_only()
    async def embedset_guild(self, ctx: commands.Context, enabled: bool = None):
        """Set the server's embed setting.

        If set, this is used instead of the global default to determine whether or not to use embeds.
        This is used for all commands done in a server.

        If enabled is left blank, the setting will be unset and the global default will be used instead.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset server False` - Disables embeds on this server.
            - `;embedset server` - Resets value to use global default.

        **Arguments:**
            - `[enabled]` - Whether to use embeds on this server. Leave blank to reset to default.

        """
        if enabled is None:
            await self.bot._config.guild(ctx.guild).embeds.clear()
            await ctx.send("Embeds will now fall back to the global setting.")
            return

        await self.bot._config.guild(ctx.guild).embeds.set(enabled)
        await ctx.send("Embeds are now enabled for this guild." if enabled else ("Embeds are now disabled for this guild."))

    @checks.guildowner_or_permissions(administrator=True)
    @embedset.group(name="command", invoke_without_command=True)
    async def embedset_command(self, ctx: commands.Context, command: CommandConverter, enabled: bool = None) -> None:
        """Sets a command's embed setting.

        If you're the bot owner, this will try to change the command's embed setting globally by default.
        Otherwise, this will try to change embed settings on the current server.

        If enabled is left blank, the setting will be unset.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset command info` - Clears command specific embed settings for 'info'.
            - `;embedset command info False` - Disables embeds for 'info'.
            - `;embedset command "ignore list" True` - Quotes are needed for subcommands.

        **Arguments:**
            - `[enabled]` - Whether to use embeds for this command. Leave blank to reset to default.

        """
        # Select the scope based on the author's privileges
        if await ctx.bot.is_owner(ctx.author):
            await self.embedset_command_global(ctx, command, enabled)
        else:
            await self.embedset_command_guild(ctx, command, enabled)

    def _check_if_command_requires_embed_links(self, command_obj: commands.Command) -> None:
        for command in itertools.chain((command_obj,), command_obj.parents):
            if command.requires.bot_perms.embed_links:
                # a slight abuse of this exception to save myself two lines later...
                msg = "The passed command requires Embed Links permission and therefore cannot be set to not use embeds."
                raise commands.UserFeedbackCheckFailure(msg)

    @commands.is_owner()
    @embedset_command.command(name="global")
    async def embedset_command_global(self, ctx: commands.Context, command: CommandConverter, enabled: bool = None):
        """Sets a command's embed setting globally.

        If set, this is used instead of the global default to determine whether or not to use embeds.

        If enabled is left blank, the setting will be unset.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset command global info` - Clears command specific embed settings for 'info'.
            - `;embedset command global info False` - Disables embeds for 'info'.
            - `;embedset command global "ignore list" True` - Quotes are needed for subcommands.

        **Arguments:**
            - `[enabled]` - Whether to use embeds for this command. Leave blank to reset to default.

        """
        self._check_if_command_requires_embed_links(command)
        # qualified name might be different if alias was passed to this command
        command_name = command.qualified_name

        if enabled is None:
            await self.bot._config.custom("COMMAND", command_name, 0).embeds.clear()
            await ctx.send("Embeds will now fall back to the global setting.")
            return

        await self.bot._config.custom("COMMAND", command_name, 0).embeds.set(enabled)
        if enabled:
            await ctx.send(f"Embeds are now enabled for {inline(command_name)} command.")
        else:
            await ctx.send(f"Embeds are now disabled for {inline(command_name)} command.")

    @commands.guild_only()
    @embedset_command.command(name="server", aliases=["guild"])
    async def embedset_command_guild(self, ctx: commands.GuildContext, command: CommandConverter, enabled: bool = None):
        """Sets a commmand's embed setting for the current server.

        If set, this is used instead of the server default to determine whether or not to use embeds.

        If enabled is left blank, the setting will be unset and the server default will be used instead.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset command server info` - Clears command specific embed settings for 'info'.
            - `;embedset command server info False` - Disables embeds for 'info'.
            - `;embedset command server "ignore list" True` - Quotes are needed for subcommands.

        **Arguments:**
            - `[enabled]` - Whether to use embeds for this command. Leave blank to reset to default.

        """
        self._check_if_command_requires_embed_links(command)
        # qualified name might be different if alias was passed to this command
        command_name = command.qualified_name

        if enabled is None:
            await self.bot._config.custom("COMMAND", command_name, ctx.guild.id).embeds.clear()
            await ctx.send("Embeds will now fall back to the server setting.")
            return

        await self.bot._config.custom("COMMAND", command_name, ctx.guild.id).embeds.set(enabled)
        if enabled:
            await ctx.send(f"Embeds are now enabled for {inline(command_name)} command.")
        else:
            await ctx.send(f"Embeds are now disabled for {inline(command_name)} command.")

    @embedset.command(name="channel")
    @checks.guildowner_or_permissions(administrator=True)
    @commands.guild_only()
    async def embedset_channel(self, ctx: commands.Context, enabled: bool = None):
        """Set's a channel's embed setting.

        If set, this is used instead of the guild and command defaults to determine whether or not to use embeds.
        This is used for all commands done in a channel.

        If enabled is left blank, the setting will be unset and the guild default will be used instead.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset channel False` - Disables embeds in this channel.
            - `;embedset channel` - Resets value to use guild default.

        **Arguments:**
            - `[enabled]` - Whether to use embeds in this channel. Leave blank to reset to default.

        """
        if enabled is None:
            await self.bot._config.channel(ctx.channel).embeds.clear()
            await ctx.send("Embeds will now fall back to the global setting.")
            return

        await self.bot._config.channel(ctx.channel).embeds.set(enabled)
        await ctx.send(f'Embeds are now {"enabled" if enabled else "disabled"} for this channel.')

    @embedset.command(name="user")
    async def embedset_user(self, ctx: commands.Context, enabled: bool = None):
        """Sets personal embed setting for DMs.

        If set, this is used instead of the global default to determine whether or not to use embeds.
        This is used for all commands executed in a DM with the bot.

        If enabled is left blank, the setting will be unset and the global default will be used instead.

        To see full evaluation order of embed settings, run `;help embedset`.

        **Examples:**
            - `;embedset user False` - Disables embeds in your DMs.
            - `;embedset user` - Resets value to use global default.

        **Arguments:**
            - `[enabled]` - Whether to use embeds in your DMs. Leave blank to reset to default.

        """
        if enabled is None:
            await self.bot._config.user(ctx.author).embeds.clear()
            await ctx.send("Embeds will now fall back to the global setting.")
            return

        await self.bot._config.user(ctx.author).embeds.set(enabled)
        await ctx.send("Embeds are now enabled for you in DMs." if enabled else ("Embeds are now disabled for you in DMs."))

    @commands.command(hidden=True)
    @checks.is_owner()
    async def traceback(self, ctx: commands.Context, public: bool = False):
        """Sends to the owner the last command exception that has occurred.

        If public (yes is specified), it will be sent to the chat instead.

        Warning: Sending the traceback publicly can accidentally reveal sensitive information about your computer or configuration.

        **Examples:**
            - `;traceback` - Sends the traceback to your DMs.
            - `;traceback True` - Sends the last traceback in the current context.

        **Arguments:**
            - `[public]` - Whether to send the traceback to the current context. Leave blank to send to your DMs.

        """
        destination = ctx.channel if public else ctx.author
        if self.bot._last_exception:
            for page in pagify(self.bot._last_exception, shorten_by=10):
                try:
                    await destination.send(box(page, lang="py"))
                except discord.HTTPException:
                    await ctx.channel.send("I couldn't send the traceback message to you in DM. Either you blocked me or you disabled DMs in this server.")
                    return
        else:
            await ctx.send("No exception has occurred yet.")

    @commands.command(hidden=True)
    @commands.check(CoreLogic._can_get_invite_url)
    async def invite(self, ctx):
        """Shows [botname]'s invite url.

        This will always send the invite to DMs to keep it private.

        """
        try:
            await ctx.send(await self._invite_url())
        except discord.errors.Forbidden:
            await ctx.send("I couldn't send the invite message to you in DM. Either you blocked me or you disabled DMs in this server.")

    @commands.group(hidden=True)
    @checks.is_owner()
    async def inviteset(self, ctx):
        """Commands to setup [botname]'s invite settings."""

    @inviteset.command()
    async def public(self, ctx, confirm: bool = False):
        """Toggles if `;invite` should be accessible for the average user.

        The bot must be made into a `Public bot` in the developer dashboard for public invites to work.

        **Example:**
            - `;inviteset public yes` - Toggles the public invite setting.

        **Arguments:**
            - `[confirm]` - Required to set to public. Not required to toggle back to private.

        """
        if await self.bot._config.invite_public():
            await self.bot._config.invite_public.set(False)
            await ctx.send("The invite is now private.")
            return
        app_info = await self.bot.application_info()
        if not app_info.bot_public:
            await ctx.send(
                f"I am not a public bot. That means that nobody except you can invite me on new servers.\n\nYou can change this by ticking `Public bot` in your token settings: https://discord.com/developers/applications/{self.bot.user.id}/bot",
            )
            return
        if not confirm:
            await ctx.send(
                f"You're about to make the `{ctx.clean_prefix}invite` command public. All users will be able to invite me on their server.\n\nIf you agree, you can type `{ctx.clean_prefix}inviteset public yes`.",
            )
        else:
            await self.bot._config.invite_public.set(True)
            await ctx.send("The invite command is now public.")

    @inviteset.command()
    async def perms(self, ctx, level: int):
        """Make the bot create its own role with permissions on join.

        The bot will create its own role with the desired permissions when it joins a new server. This is a special role that can't be deleted or removed from the bot.

        For that, you need to provide a valid permissions level.
        You can generate one here: https://discordapi.com/permissions.html

        Please note that you might need two factor authentication for some permissions.

        **Example:**
            - `;inviteset perms 134217728` - Adds a "Manage Nicknames" permission requirement to the invite.

        **Arguments:**
            - `<level>` - The permission level to require for the bot in the generated invite.

        """
        await self.bot._config.invite_perm.set(level)
        await ctx.send("The new permissions level has been set.")

    @inviteset.command()
    async def commandscope(self, ctx: commands.Context):
        """Add the `applications.commands` scope to your invite URL.

        This allows the usage of slash commands on the servers that
        invited your bot with that scope.

        Note that previous servers that invited the bot without the
        scope cannot have slash commands, they will have to invite the
        bot a second time.

        """
        enabled = not await self.bot._config.invite_commands_scope()
        await self.bot._config.invite_commands_scope.set(enabled)
        if enabled:
            await ctx.send("The `applications.commands` scope has been added to the invite URL.")
        else:
            await ctx.send("The `applications.commands` scope has been removed from the invite URL.")

    @commands.command(hidden=True)
    @checks.is_owner()
    async def leave(self, ctx: commands.Context, *servers: GuildConverter):
        """Leaves servers.

        If no server IDs are passed the local server will be left instead.

        Note: This command is interactive.

        **Examples:**
            - `;leave` - Leave the current server.
            - `;leave "Melanie - Discord Bot"` - Quotes are necessary when there are spaces in the name.
            - `;leave 133049272517001216 240154543684321280` - Leaves multiple servers, using IDs.

        **Arguments:**
            - `[servers...]` - The servers to leave. When blank, attempts to leave the current server.

        """
        guilds = servers
        if ctx.guild is None and not guilds:
            return await ctx.send("You need to specify at least one server ID.")

        leaving_local_guild = not guilds

        if leaving_local_guild:
            guilds = (ctx.guild,)
            msg = "You haven't passed any server ID. Do you want me to leave this server?" + " (y/n)"
        else:
            msg = "Are you sure you want me to leave these servers?" + " (y/n):\n" + "\n".join(f"- {guild.name} (`{guild.id}`)" for guild in guilds)

        for guild in guilds:
            if guild.owner.id == ctx.me.id:
                return await ctx.send(f"I cannot leave the server `{guild.name}`: I am the owner of it.")

        for page in pagify(msg):
            await ctx.send(page)
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("Response timed out.")
            return
        else:
            if pred.result is True:
                if leaving_local_guild:
                    await ctx.send("Alright. Bye :wave:")
                else:
                    await ctx.send(f"Alright. Leaving {len(guilds)} servers...")
                for guild in guilds:
                    logger.warning("Leaving guild '{}' ({})", guild.name, guild.id)
                    await guild.leave()
            elif leaving_local_guild:
                await ctx.send("Alright, I'll stay then. :)")
            else:
                await ctx.send("Alright, I'm not leaving those servers.")

    @commands.command(hidden=True)
    @checks.is_owner()
    async def servers(self, ctx: commands.Context):
        """Lists the servers [botname] is currently in.

        Note: This command is interactive.

        """
        guilds = sorted(self.bot.guilds, key=lambda s: s.name.lower())
        msg = "\n".join(f"{guild.name} (`{guild.id}`)\n" for guild in guilds)

        pages = list(pagify(msg, ["\n"], page_length=1000))

        if len(pages) == 1:
            await ctx.send(pages[0])
        else:
            await menu(ctx, pages, DEFAULT_CONTROLS)

    @commands.command(require_var_positional=True)
    @checks.is_owner()
    async def load(self, ctx: commands.Context, *cogs: str):
        """Loads cog packages from the local paths and installed cogs.

        See packages available to load with `;cogs`.

        Additional cogs can be added using Downloader, or from local paths using `;addpath`.

        **Examples:**
            - `;load general` - Loads the `general` cog.
            - `;load admin mod mutes` - Loads multiple cogs.

        **Arguments:**
            - `<cogs...>` - The cog packages to load.

        """
        cogs = tuple(cog.rstrip(",") for cog in cogs)
        async with ctx.typing():
            (loaded, failed, invalid_pkg_names, not_found, already_loaded, failed_with_reason, repos_with_shared_libs) = await self._load(cogs)

        output = []

        if loaded:
            loaded_packages = humanize_list([inline(package) for package in loaded])
            formed = f"Loaded {loaded_packages}."
            output.append(formed)

        if already_loaded:
            if len(already_loaded) == 1:
                formed = f"The following package is already loaded: {inline(already_loaded[0])}"
            else:
                formed = f"The following packages are already loaded: {humanize_list([inline(package) for package in already_loaded])}"
            output.append(formed)

        if failed:
            if len(failed) == 1:
                formed = f"Failed to load the following package: {inline(failed[0])}."
            else:
                formed = f"Failed to load the following packages: {humanize_list([inline(package) for package in failed])}"
            output.append(formed)

        if invalid_pkg_names:
            if len(invalid_pkg_names) == 1:
                formed = (
                    "The following name is not a valid package name: {pack}\nPackage names cannot start with a number and can only contain ascii numbers, letters, and underscores."
                ).format(pack=inline(invalid_pkg_names[0]))
            else:
                formed = (
                    "The following names are not valid package names: {packs}\nPackage names cannot start with a number and can only contain ascii numbers, letters, and underscores."
                ).format(packs=humanize_list([inline(package) for package in invalid_pkg_names]))
            output.append(formed)

        if not_found:
            if len(not_found) == 1:
                formed = f"The following package was not found in any cog path: {inline(not_found[0])}."
            else:
                formed = f"The following packages were not found in any cog path: {humanize_list([inline(package) for package in not_found])}"
            output.append(formed)

        if failed_with_reason:
            reasons = "\n".join(f"`{x}`: {y}" for x, y in failed_with_reason)
            if len(failed_with_reason) == 1:
                formed = f"This package could not be loaded for the following reason:\n{reasons}"
            else:
                formed = f"These packages could not be loaded for the following reasons:\n{reasons}"
            output.append(formed)

        if repos_with_shared_libs:
            if len(repos_with_shared_libs) == 1:
                formed = (
                    "**WARNING**: The following repo is using shared libs which are marked for removal in the future: {repo}.\nYou should inform maintainer of the repo about this message."
                ).format(repo=inline(repos_with_shared_libs.pop()))
            else:
                formed = (
                    "**WARNING**: The following repos are using shared libs which are marked for removal in the future: {repos}.\nYou should inform maintainers of these repos about this message."
                ).format(repos=humanize_list([inline(repo) for repo in repos_with_shared_libs]))
            output.append(formed)

        if output:
            total_message = "\n".join(output)
            for page in pagify(total_message, delims=["\n", ", "], priority=True, page_length=1500):
                if page.startswith(", "):
                    page = page[2:]
                await ctx.send(page)

    @commands.command(require_var_positional=True)
    @checks.is_owner()
    async def unload(self, ctx: commands.Context, *cogs: str):
        """Unloads previously loaded cog packages.

        See packages available to unload with `;cogs`.

        **Examples:**
            - `;unload general` - Unloads the `general` cog.
            - `;unload admin mod mutes` - Unloads multiple cogs.

        **Arguments:**
            - `<cogs...>` - The cog packages to unload.

        """
        cogs = tuple(cog.rstrip(",") for cog in cogs)
        unloaded, failed = await self._unload(cogs)

        output = []

        if unloaded:
            if len(unloaded) == 1:
                formed = f"The following package was unloaded: {inline(unloaded[0])}."
            else:
                formed = f"The following packages were unloaded: {humanize_list([inline(package) for package in unloaded])}."
            output.append(formed)

        if failed:
            if len(failed) == 1:
                formed = f"The following package was not loaded: {inline(failed[0])}."
            else:
                formed = f"The following packages were not loaded: {humanize_list([inline(package) for package in failed])}."
            output.append(formed)

        if output:
            total_message = "\n".join(output)
            for page in pagify(total_message):
                await ctx.send(page)

    @commands.command(require_var_positional=True)
    @checks.is_owner()
    async def reload(self, ctx: commands.Context, *cogs: str):
        """Reloads cog packages.

        This will unload and then load the specified cogs.

        Cogs that were not loaded will only be loaded.

        **Examples:**
            - `;reload general` - Unloads then loads the `general` cog.
            - `;reload admin mod mutes` - Unloads then loads multiple cogs.

        **Arguments:**
            - `<cogs...>` - The cog packages to reload.

        """
        cogs = tuple(cog.rstrip(",") for cog in cogs)

        async with ctx.typing():
            (loaded, failed, invalid_pkg_names, not_found, already_loaded, failed_with_reason, repos_with_shared_libs) = await self._reload(
                [c for c in cogs if c != "melanie"],
            )
            for cog in cogs:
                meth = partial(reload_module_dask, cog)

                try:
                    await self.bot.dask.run(meth)
                    await ctx.send(f"distributed reloaded `{cog}`", delete_after=10)
                except Exception as e:
                    fmte = stackprinter.format(e, show_vals=False)
                    for pg in pagify(fmte, page_length=1700):
                        await ctx.send(f"``{pg}`", delete_after=10)

        output = []

        if loaded:
            loaded_packages = humanize_list([inline(package) for package in loaded])
            formed = f"bot reloaded {loaded_packages}"
            output.append(formed)

        if failed:
            if len(failed) == 1:
                formed = f"Failed to reload the following package: {inline(failed[0])}."
            else:
                formed = f"Failed to reload the following packages: {humanize_list([inline(package) for package in failed])}"
            output.append(formed)

        if invalid_pkg_names:
            if len(invalid_pkg_names) == 1:
                formed = (
                    "The following name is not a valid package name: {pack}\nPackage names cannot start with a number and can only contain ascii numbers, letters, and underscores."
                ).format(pack=inline(invalid_pkg_names[0]))
            else:
                formed = (
                    "The following names are not valid package names: {packs}\nPackage names cannot start with a number and can only contain ascii numbers, letters, and underscores."
                ).format(packs=humanize_list([inline(package) for package in invalid_pkg_names]))
            output.append(formed)

        if failed_with_reason:
            reasons = "\n".join(f"`{x}`: {y}" for x, y in failed_with_reason)
            if len(failed_with_reason) == 1:
                formed = f"This package could not be reloaded for the following reason:\n{reasons}"
            else:
                formed = f"These packages could not be reloaded for the following reasons:\n{reasons}"
            output.append(formed)

        if repos_with_shared_libs:
            if len(repos_with_shared_libs) == 1:
                formed = (
                    "**WARNING**: The following repo is using shared libs which are marked for removal in the future: {repo}.\nYou should inform maintainers of these repos about this message."
                ).format(repo=inline(repos_with_shared_libs.pop()))
            else:
                formed = (
                    "**WARNING**: The following repos are using shared libs which are marked for removal in the future: {repos}.\nYou should inform maintainers of these repos about this message."
                ).format(repos=humanize_list([inline(repo) for repo in repos_with_shared_libs]))
            output.append(formed)

        if output:
            total_message = "\n".join(output)
            for page in pagify(total_message):
                await ctx.send(page)

    @commands.command(name="shutdown")
    @checks.is_owner()
    async def _shutdown(self, ctx: commands.Context, silently: bool = False):
        """Shuts down the bot.

        Allows [botname] to shut down gracefully.

        This is the recommended method for shutting down the bot.

        **Examples:**
            - `;shutdown`
            - `;shutdown True` - Shutdowns silently.

        **Arguments:**
            - `[silently]` - Whether to skip sending the shutdown message. Defaults to False.

        """
        with contextlib.suppress(discord.HTTPException):
            if not silently:
                wave = "\N{WAVING HAND SIGN}"
                skin = "\N{EMOJI MODIFIER FITZPATRICK TYPE-3}"
                await ctx.send(f"Shutting down... {wave}{skin}")
        await ctx.bot.shutdown()

    @commands.command(name="restart")
    @checks.is_owner()
    async def _restart(self, ctx: commands.Context, silently: bool = False):
        """Attempts to restart [botname].

        Makes [botname] quit with exit code 26.
        The restart is not guaranteed: it must be dealt with by the process manager in use.

        **Examples:**
            - `;restart`
            - `;restart True` - Restarts silently.

        **Arguments:**
            - `[silently]` - Whether to skip sending the restart message. Defaults to False.

        """
        with contextlib.suppress(discord.HTTPException):
            if not silently:
                await ctx.send("Restarting...")
        await ctx.bot.shutdown(restart=True)

    @commands.group(name="set", hidden=True)
    async def _set(self, ctx: commands.Context):
        """Commands for changing [botname]'s settings."""

    @checks.is_owner()
    @_set.command("showsettings")
    async def set_showsettings(self, ctx: commands.Context):
        """Show the current settings for [botname]."""
        if ctx.guild:
            guild_data = await ctx.bot._config.guild(ctx.guild).all()
            guild = ctx.guild
            admin_role_ids = guild_data["admin_role"]
            admin_role_names = [r.name for r in guild.roles if r.id in admin_role_ids]
            admin_roles_str = humanize_list(admin_role_names) if admin_role_names else "Not Set."
            mod_role_ids = guild_data["mod_role"]
            mod_role_names = [r.name for r in guild.roles if r.id in mod_role_ids]
            mod_roles_str = humanize_list(mod_role_names) if mod_role_names else "Not Set."

            guild_locale = await i18n.get_locale_from_guild(self.bot, ctx.guild)
            guild_regional_format = await i18n.get_regional_format_from_guild(self.bot, ctx.guild) or guild_locale

            guild_settings = f"Admin roles: {admin_roles_str}\nMod roles: {mod_roles_str}\nLocale: {guild_locale}\nRegional format: {guild_regional_format}\n"
        else:
            guild_settings = ""

        prefixes = await ctx.bot._prefix_cache.get_prefixes(ctx.guild)
        global_data = await ctx.bot._config.all()
        locale = global_data["locale"]
        regional_format = global_data["regional_format"] or locale
        colour = discord.Colour(global_data["color"])

        prefix_string = " ".join(prefixes)
        settings = (
            "{bot_name} Settings:\nPrefixes: {prefixes}\n{guild_settings}Global locale: {locale}\nGlobal regional format: {regional_format}\nDefault embed colour: {colour}"
        ).format(
            bot_name=ctx.bot.user.name,
            prefixes=prefix_string,
            guild_settings=guild_settings,
            locale=locale,
            regional_format=regional_format,
            colour=colour,
        )
        for page in pagify(settings):
            await ctx.send(box(page))

    @checks.is_owner()
    @_set.command(name="deletedelay")
    @commands.guild_only()
    async def deletedelay(self, ctx: commands.Context, time: int = None):
        """Set the delay until the bot removes the command message.

        Must be between -1 and 60.

        Set to -1 to disable this feature.

        This is only applied to the current server and not globally.

        **Examples:**
            - `;set deletedelay` - Shows the current delete delay setting.
            - `;set deletedelay 60` - Sets the delete delay to the max of 60 seconds.
            - `;set deletedelay -1` - Disables deleting command messages.

        **Arguments:**
            - `[time]` - The seconds to wait before deleting the command message. Use -1 to disable.

        """
        guild = ctx.guild
        if time is not None:
            time = min(max(time, -1), 60)  # Enforces the time limits
            await ctx.bot._config.guild(guild).delete_delay.set(time)
            if time == -1:
                await ctx.send("Command deleting disabled.")
            else:
                await ctx.send(f"Delete delay set to {time} seconds.")
        else:
            delay = await ctx.bot._config.guild(guild).delete_delay()
            if delay != -1:
                await ctx.send(f"Bot will delete command messages after {delay} seconds. Set this value to -1 to stop deleting messages")
            else:
                await ctx.send("I will not delete command messages.")

    @checks.is_owner()
    @_set.command(name="description")
    async def setdescription(self, ctx: commands.Context, *, description: str = ""):
        """Sets the bot's description.

        Use without a description to reset.
        This is shown in a few locations, including the help menu.

        The maximum description length is 250 characters to ensure it displays properly.

        The default is "Melanie V3".

        **Examples:**
            - `;set description` - Resets the description to the default setting.
            - `;set description MyBot: A Melanie V3 Bot`

        **Arguments:**
            - `[description]` - The description to use for this bot. Leave blank to reset to the default.

        """
        if not description:
            await ctx.bot._config.description.clear()
            ctx.bot.description = "Melanie V3"
            await ctx.send("Description reset.")
        elif len(description) > 250:  # While the limit is 256, we bold it adding characters.
            await ctx.send("This description is too long to properly display. Please try again with below 250 characters.")
        else:
            await ctx.bot._config.description.set(description)
            ctx.bot.description = description
            await ctx.tick()

    @_set.command()
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def addadminrole(self, ctx: commands.Context, *, role: discord.Role):
        """Adds an admin role for this guild.

        Admins have the same access as Mods, plus additional admin level commands like:
         - `;set serverprefix`
         - `;addrole`
         - `;ban`
         - `;ignore guild`

         And more.

         **Examples:**
            - `;set addadminrole @Admins`
            - `;set addadminrole Super Admins`

        **Arguments:**
            - `<role>` - The role to add as an admin.

        """
        async with ctx.bot._config.guild(ctx.guild).admin_role() as roles:
            if role.id in roles:
                return await ctx.send("This role is already an admin role.")
            roles.append(role.id)
        await ctx.send("That role is now considered an admin role.")

    @_set.command()
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def addmodrole(self, ctx: commands.Context, *, role: discord.Role):
        """Adds a moderator role for this guild.

        This grants access to moderator level commands like:
         - `;mute`
         - `;cleanup`
         - `;customcommand create`

         And more.

         **Examples:**
            - `;set addmodrole @Mods`
            - `;set addmodrole Loyal Helpers`

        **Arguments:**
            - `<role>` - The role to add as a moderator.

        """
        async with ctx.bot._config.guild(ctx.guild).mod_role() as roles:
            if role.id in roles:
                return await ctx.send("This role is already a mod role.")
            roles.append(role.id)
        await ctx.send("That role is now considered a mod role.")

    @_set.command(aliases=["remadmindrole", "deladminrole", "deleteadminrole"])
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def removeadminrole(self, ctx: commands.Context, *, role: discord.Role):
        """Removes an admin role for this guild.

        **Examples:**
            - `;set removeadminrole @Admins`
            - `;set removeadminrole Super Admins`

        **Arguments:**
            - `<role>` - The role to remove from being an admin.

        """
        async with ctx.bot._config.guild(ctx.guild).admin_role() as roles:
            if role.id not in roles:
                return await ctx.send("That role was not an admin role to begin with.")
            roles.remove(role.id)
        await ctx.send("That role is no longer considered an admin role.")

    @_set.command(aliases=["remmodrole", "delmodrole", "deletemodrole"])
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def removemodrole(self, ctx: commands.Context, *, role: discord.Role):
        """Removes a mod role for this guild.

        **Examples:**
            - `;set removemodrole @Mods`
            - `;set removemodrole Loyal Helpers`

        **Arguments:**
            - `<role>` - The role to remove from being a moderator.

        """
        async with ctx.bot._config.guild(ctx.guild).mod_role() as roles:
            if role.id not in roles:
                return await ctx.send("That role was not a mod role to begin with.")
            roles.remove(role.id)
        await ctx.send("That role is no longer considered a mod role.")

    @checks.is_owner()
    @_set.command(aliases=["usebotcolor"])
    @commands.guild_only()
    async def usebotcolour(self, ctx: commands.Context):
        """Toggle whether to use the bot owner-configured colour for embeds.

        Default is to use the bot's configured colour.
        Otherwise, the colour used will be the colour of the bot's top role.

        **Example:**
            - `;set usebotcolour`

        """
        current_setting = await ctx.bot._config.guild(ctx.guild).use_bot_color()
        await ctx.bot._config.guild(ctx.guild).use_bot_color.set(not current_setting)
        await ctx.send(f'The bot {"will" if current_setting else "will not"} use its configured color for embeds.')

    @checks.is_owner()
    @_set.command()
    @commands.guild_only()
    async def serverfuzzy(self, ctx: commands.Context):
        """Toggle whether to enable fuzzy command search for the server.

        This allows the bot to identify potential misspelled commands and offer corrections.

        Note: This can be processor intensive and may be unsuitable for larger servers.

        Default is for fuzzy command search to be disabled.

        **Example:**
            - `;set serverfuzzy`

        """
        current_setting = await ctx.bot._config.guild(ctx.guild).fuzzy()
        await ctx.bot._config.guild(ctx.guild).fuzzy.set(not current_setting)
        await ctx.send(f'Fuzzy command search has been {"disabled" if current_setting else "enabled"} for this server.')

    @_set.command()
    @checks.is_owner()
    async def fuzzy(self, ctx: commands.Context):
        """Toggle whether to enable fuzzy command search in DMs.

        This allows the bot to identify potential misspelled commands and offer corrections.

        Default is for fuzzy command search to be disabled.

        **Example:**
            - `;set fuzzy`

        """
        current_setting = await ctx.bot._config.fuzzy()
        await ctx.bot._config.fuzzy.set(not current_setting)
        await ctx.send(f'Fuzzy command search has been {"disabled" if current_setting else "enabled"} in DMs.')

    @_set.command(aliases=["color"])
    @checks.is_owner()
    async def colour(self, ctx: commands.Context, *, colour: discord.Colour = None):
        """Sets a default colour to be used for the bot's embeds.

        Acceptable values for the colour parameter can be found at:

        https://discordpy.readthedocs.io/en/stable/ext/commands/api.html#discord.ext.commands.ColourConverter

        **Examples:**
            - `;set colour dark melanie`
            - `;set colour blurple`
            - `;set colour 0x5DADE2`
            - `;set color 0x#FDFEFE`
            - `;set color #7F8C8D`

        **Arguments:**
            - `[colour]` - The colour to use for embeds. Leave blank to set to the default value (melanie).

        """
        if colour is None:
            ctx.bot._color = discord.Color.melanie()
            await ctx.bot._config.color.set(discord.Color.melanie().value)
            return await ctx.send("The color has been reset.")
        ctx.bot._color = colour
        await ctx.bot._config.color.set(colour.value)
        await ctx.send("The color has been set.")

    @_set.group(invoke_without_command=True)
    @checks.is_owner()
    async def avatar(self, ctx: commands.Context, url: str = None):
        """Sets [botname]'s avatar.

        Supports either an attachment or an image URL.

        **Examples:**
            - `;set avatar` - With an image attachment, this will set the avatar.
            - `;set avatar` - Without an attachment, this will show the command help.
            - `;set avatar https://links.flaree.xyz/k95` - Sets the avatar to the provided url.

        **Arguments:**
            - `[url]` - An image url to be used as an avatar. Leave blank when uploading an attachment.

        """
        if len(ctx.message.attachments) > 0:  # Attachments take priority
            data = await ctx.message.attachments[0].read()
        elif url is not None:
            if url.startswith("<") and url.endswith(">"):
                url = url[1:-1]

            r = await self.bot.htx.aclose(url)
            data = r.content

        else:
            await ctx.send_help()
            return

        try:
            async with ctx.typing():
                await ctx.bot.user.edit(avatar=data)
        except discord.HTTPException:
            await ctx.send(
                "Failed. Remember that you can edit my avatar up to two times a hour. The URL or attachment must be a valid image in either JPG or PNG format.",
            )
        except discord.InvalidArgument:
            await ctx.send("JPG / PNG format only.")
        else:
            await ctx.send("Done.")

    @avatar.command(name="remove", aliases=["clear"])
    @checks.is_owner()
    async def avatar_remove(self, ctx: commands.Context):
        """Removes [botname]'s avatar.

        **Example:**
            - `;set avatar remove`

        """
        async with ctx.typing():
            await ctx.bot.user.edit(avatar=None)
        await ctx.send("Avatar removed.")

    @_set.command(name="playing", aliases=["game"])
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def _game(self, ctx: commands.Context, *, game: str = None):
        """Sets [botname]'s playing status.

        This will appear as `Playing <game>` or `PLAYING A GAME: <game>` depending on the context.

        Maximum length for a playing status is 128 characters.

        **Examples:**
            - `;set playing` - Clears the activity status.
            - `;set playing the keyboard`

        **Arguments:**
            - `[game]` - The text to follow `Playing`. Leave blank to clear the current activity status.

        """
        if game:
            if len(game) > 128:
                await ctx.send("The maximum length of game descriptions is 128 characters.")
                return
            game = discord.Game(name=game)
        else:
            game = None
        status = ctx.bot.guilds[0].me.status if len(ctx.bot.guilds) > 0 else discord.Status.online
        await ctx.bot.change_presence(status=status, activity=game)
        if game:
            await ctx.send(f"Status set to ``Playing {game.name}``.")
        else:
            await ctx.send("Game cleared.")

    @_set.command(name="listening")
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def _listening(self, ctx: commands.Context, *, listening: str = None):
        """Sets [botname]'s listening status.

        This will appear as `Listening to <listening>`.

        Maximum length for a listening status is 128 characters.

        **Examples:**
            - `;set listening` - Clears the activity status.
            - `;set listening jams`

        **Arguments:**
            - `[listening]` - The text to follow `Listening to`. Leave blank to clear the current activity status.

        """
        status = ctx.bot.guilds[0].me.status if len(ctx.bot.guilds) > 0 else discord.Status.online
        if listening:
            if len(listening) > 128:
                await ctx.send("The maximum length of listening descriptions is 128 characters.")
                return
            activity = discord.Activity(name=listening, type=discord.ActivityType.listening)
        else:
            activity = None
        await ctx.bot.change_presence(status=status, activity=activity)
        if activity:
            await ctx.send(f"Status set to ``Listening to {listening}``.")
        else:
            await ctx.send("Listening cleared.")

    @_set.command(name="watching")
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def _watching(self, ctx: commands.Context, *, watching: str = None):
        """Sets [botname]'s watching status.

        This will appear as `Watching <watching>`.

        Maximum length for a watching status is 128 characters.

        **Examples:**
            - `;set watching` - Clears the activity status.
            - `;set watching ;help`

        **Arguments:**
            - `[watching]` - The text to follow `Watching`. Leave blank to clear the current activity status.

        """
        status = ctx.bot.guilds[0].me.status if len(ctx.bot.guilds) > 0 else discord.Status.online
        if watching:
            if len(watching) > 128:
                await ctx.send("The maximum length of watching descriptions is 128 characters.")
                return
            activity = discord.Activity(name=watching, type=discord.ActivityType.watching)
        else:
            activity = None
        await ctx.bot.change_presence(status=status, activity=activity)
        if activity:
            await ctx.send(f"Status set to ``Watching {watching}``.")
        else:
            await ctx.send("Watching cleared.")

    @_set.command(name="competing")
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def _competing(self, ctx: commands.Context, *, competing: str = None):
        """Sets [botname]'s competing status.

        This will appear as `Competing in <competing>`.

        Maximum length for a competing status is 128 characters.

        **Examples:**
            - `;set competing` - Clears the activity status.
            - `;set competing London 2012 Olympic Games`

        **Arguments:**
            - `[competing]` - The text to follow `Competing in`. Leave blank to clear the current activity status.

        """
        status = ctx.bot.guilds[0].me.status if len(ctx.bot.guilds) > 0 else discord.Status.online
        if competing:
            if len(competing) > 128:
                await ctx.send("The maximum length of competing descriptions is 128 characters.")
                return
            activity = discord.Activity(name=competing, type=discord.ActivityType.competing)
        else:
            activity = None
        await ctx.bot.change_presence(status=status, activity=activity)
        if activity:
            await ctx.send(f"Status set to ``Competing in {competing}``.")
        else:
            await ctx.send("Competing cleared.")

    @_set.command()
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def status(self, ctx: commands.Context, *, status: str):
        """Sets [botname]'s status.

        Available statuses:
            - `online`
            - `idle`
            - `dnd`
            - `invisible`

        **Examples:**
            - `;set status online` - Clears the status.
            - `;set status invisible`

        **Arguments:**
            - `<status>` - One of the available statuses.

        """
        statuses = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd, "invisible": discord.Status.invisible}

        game = ctx.bot.guilds[0].me.activity if len(ctx.bot.guilds) > 0 else None
        try:
            status = statuses[status.lower()]
        except KeyError:
            await ctx.send_help()
        else:
            await ctx.bot.change_presence(status=status, activity=game)
            await ctx.send(f"Status changed to {status}.")

    @_set.command(name="streaming", aliases=["stream", "twitch"], usage="[(<streamer> <stream_title>)]")
    @checks.bot_in_a_guild()
    @checks.is_owner()
    async def stream(self, ctx: commands.Context, streamer=None, *, stream_title=None):
        """Sets [botname]'s streaming status to a twitch stream.

        This will appear as `Streaming <stream_title>` or `LIVE ON TWITCH` depending on the context.
        It will also include a `Watch` button with a twitch.tv url for the provided streamer.

        Maximum length for a stream title is 128 characters.

        Leaving both streamer and stream_title empty will clear it.

        **Examples:**
            - `;set stream` - Clears the activity status.
            - `;set stream 26 Twentysix is streaming` - Sets the stream to `https://www.twitch.tv/26`.
            - `;set stream https://twitch.tv/26 Twentysix is streaming` - Sets the URL manually.

        **Arguments:**
            - `<streamer>` - The twitch streamer to provide a link to. This can be their twitch name or the entire URL.
            - `<stream_title>` - The text to follow `Streaming` in the status.

        """
        status = ctx.bot.guilds[0].me.status if len(ctx.bot.guilds) > 0 else None

        if stream_title:
            stream_title = stream_title.strip()
            if "twitch.tv/" not in streamer:
                streamer = f"https://www.twitch.tv/{streamer}"
            if len(streamer) > 511:
                await ctx.send("The maximum length of the streamer url is 511 characters.")
                return
            if len(stream_title) > 128:
                await ctx.send("The maximum length of the stream title is 128 characters.")
                return
            activity = discord.Streaming(url=streamer, name=stream_title)
            await ctx.bot.change_presence(status=status, activity=activity)
        elif streamer is not None:
            await ctx.send_help()
            return
        else:
            await ctx.bot.change_presence(activity=None, status=status)
        await ctx.send("Done.")

    @_set.command(name="username", aliases=["name"])
    @checks.is_owner()
    async def _username(self, ctx: commands.Context, *, username: str):
        """Sets [botname]'s username.

        Maximum length for a username is 32 characters.

        Note: The username of a verified bot cannot be manually changed.
            Please contact Discord support to change it.

        **Example:**
            - `;set username BaguetteBot`

        **Arguments:**
            - `<username>` - The username to give the bot.

        """
        try:
            if self.bot.user.public_flags.verified_bot:
                await ctx.send("The username of a verified bot cannot be manually changed. Please contact Discord support to change it.")
                return
            if len(username) > 32:
                await ctx.send("Failed to change name. Must be 32 characters or fewer.")
                return
            async with ctx.typing():
                await asyncio.wait_for(self._name(name=username), timeout=30)
        except asyncio.TimeoutError:
            await ctx.send(
                (
                    "Changing the username timed out. Remember that you can only do it up to 2 times an hour. Use nicknames if you need frequent changes: {command}"
                ).format(command=inline(f"{ctx.clean_prefix}set nickname")),
            )
        except discord.HTTPException as e:
            if e.code == 50035:
                error_string = e.text.split("\n")[1]  # Remove the "Invalid Form body"
                await ctx.send(f"Failed to change the username. Discord returned the following error:\n{inline(error_string)}")
            else:
                logger.opt(exception=e).error("Unexpected error occurred when trying to change the username.")
                await ctx.send("Unexpected error occurred when trying to change the username.")
        else:
            await ctx.send("Done.")

    @_set.command(name="nickname")
    @checks.admin_or_permissions(manage_nicknames=True)
    @commands.guild_only()
    async def _nickname(self, ctx: commands.Context, *, nickname: str = None):
        """Sets [botname]'s nickname for the current server.

        Maximum length for a nickname is 32 characters.

        **Example:**
            - `;set nickname 🎃 SpookyBot 🎃`

        **Arguments:**
            - `[nickname]` - The nickname to give the bot. Leave blank to clear the current nickname.

        """
        try:
            if nickname and len(nickname) > 32:
                await ctx.send("Failed to change nickname. Must be 32 characters or fewer.")
                return
            await ctx.guild.me.edit(nick=nickname)
        except discord.Forbidden:
            await ctx.send("I lack the permissions to change my own nickname.")
        else:
            await ctx.send("Done.")

    @_set.command(aliases=["prefixes"], require_var_positional=True)
    @checks.is_owner()
    async def prefix(self, ctx: commands.Context, *prefixes: str):
        """Sets [botname]'s global prefix(es).

        Warning: This is not additive. It will replace all current prefixes.

        See also the `--mentionable` flag to enable mentioning the bot as the prefix.

        **Examples:**
            - `;set prefix !`
            - `;set prefix "! "` - Quotes are needed to use spaces in prefixes.
            - `;set prefix "@[botname] "` - This uses a mention as the prefix. See also the `--mentionable` flag.
            - `;set prefix ! ? .` - Sets multiple prefixes.

        **Arguments:**
            - `<prefixes...>` - The prefixes the bot will respond to globally.

        """
        if any(len(x) > MAX_PREFIX_LENGTH for x in prefixes):
            await ctx.send("Warning: A prefix is above the recommended length (20 characters).\nDo you want to continue? (y/n)")
            pred = MessagePredicate.yes_or_no(ctx)
            try:
                await self.bot.wait_for("message", check=pred, timeout=30)
            except asyncio.TimeoutError:
                await ctx.send("Response timed out.")
                return
            else:
                if pred.result is False:
                    await ctx.send("Cancelled.")
                    return
        await ctx.bot.set_prefixes(guild=None, prefixes=prefixes)
        if len(prefixes) == 1:
            await ctx.send("Prefix set.")
        else:
            await ctx.send("Prefixes set.")

    @_set.command(aliases=["serverprefixes"])
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def serverprefix(self, ctx: commands.Context, *prefixes: str):
        """Sets [botname]'s server prefix(es).

        Warning: This will override global prefixes, the bot will not respond to any global prefixes in this server.
            This is not additive. It will replace all current server prefixes.
            A prefix cannot have more than 20 characters.

        **Examples:**
            - `;set serverprefix !`
            - `;set serverprefix "! "` - Quotes are needed to use spaces in prefixes.
            - `;set serverprefix "@[botname] "` - This uses a mention as the prefix.
            - `;set serverprefix ! ? .` - Sets multiple prefixes.

        **Arguments:**
            - `[prefixes...]` - The prefixes the bot will respond to on this server. Leave blank to clear server prefixes.

        """
        if not prefixes:
            await ctx.bot.set_prefixes(guild=ctx.guild, prefixes=[])
            await ctx.send("Server prefixes have been reset.")
            return
        if any(len(x) > MAX_PREFIX_LENGTH for x in prefixes):
            await ctx.send("You cannot have a prefix longer than 20 characters.")
            return
        prefixes = sorted(prefixes, reverse=True)
        await ctx.bot.set_prefixes(guild=ctx.guild, prefixes=prefixes)
        if len(prefixes) == 1:
            await ctx.send("Server prefix set.")
        else:
            await ctx.send("Server prefixes set.")

    @_set.command()
    @checks.is_owner()
    async def globallocale(self, ctx: commands.Context, language_code: str):
        """Changes the bot's default locale.

        This will be used when a server has not set a locale, or in DMs.

        Go to [Melanie's Crowdin page](https://translate.discord.melanie) to see locales that are available with translations.

        To reset to English, use "en-US".

        **Examples:**
            - `;set locale en-US`
            - `;set locale de-DE`
            - `;set locale fr-FR`
            - `;set locale pl-PL`

        **Arguments:**
            - `<language_code>` - The default locale to use for the bot. This can be any language code with country code included.

        """
        try:
            locale = BabelLocale.parse(language_code, sep="-")
        except (ValueError, UnknownLocaleError):
            await ctx.send("Invalid language code. Use format: `en-US`")
            return
        if locale.territory is None:
            await ctx.send("Invalid format - language code has to include country code, e.g. `en-US`")
            return
        standardized_locale_name = f"{locale.language}-{locale.territory}"
        i18n.set_locale(standardized_locale_name)
        await self.bot._i18n_cache.set_locale(None, standardized_locale_name)
        await ctx.send("Global locale has been set.")

    @_set.command()
    @commands.guild_only()
    @checks.guildowner_or_permissions(manage_guild=True)
    async def locale(self, ctx: commands.Context, language_code: str):
        """Changes the bot's locale in this server.

        Go to [Melanie's Crowdin page](https://translate.discord.melanie) to see locales that are available with translations.

        Use "default" to return to the bot's default set language.
        To reset to English, use "en-US".

        **Examples:**
            - `;set locale en-US`
            - `;set locale de-DE`
            - `;set locale fr-FR`
            - `;set locale pl-PL`
            - `;set locale default` - Resets to the global default locale.

        **Arguments:**
            - `<language_code>` - The default locale to use for the bot. This can be any language code with country code included.

        """
        if language_code.lower() == "default":
            global_locale = await self.bot._config.locale()
            i18n.set_contextual_locale(global_locale)
            await self.bot._i18n_cache.set_locale(ctx.guild, None)
            await ctx.send("Locale has been set to the default.")
            return
        try:
            locale = BabelLocale.parse(language_code, sep="-")
        except (ValueError, UnknownLocaleError):
            await ctx.send("Invalid language code. Use format: `en-US`")
            return
        if locale.territory is None:
            await ctx.send("Invalid format - language code has to include country code, e.g. `en-US`")
            return
        standardized_locale_name = f"{locale.language}-{locale.territory}"
        i18n.set_contextual_locale(standardized_locale_name)
        await self.bot._i18n_cache.set_locale(ctx.guild, standardized_locale_name)
        await ctx.send("Locale has been set.")

    @_set.command(aliases=["globalregion"])
    @commands.guild_only()
    @checks.is_owner()
    async def globalregionalformat(self, ctx: commands.Context, language_code: str = None):
        """Changes the bot's regional format. This is used for formatting date,
        time and numbers.

        `language_code` can be any language code with country code included, e.g. `en-US`, `de-DE`, `fr-FR`, `pl-PL`, etc.
        Leave `language_code` empty to base regional formatting on bot's locale.

        **Examples:**
            - `;set globalregionalformat en-US`
            - `;set globalregion de-DE`
            - `;set globalregionalformat` - Resets to the locale.

        **Arguments:**
            - `[language_code]` - The default region format to use for the bot.

        """
        if language_code is None:
            i18n.set_regional_format(None)
            await self.bot._i18n_cache.set_regional_format(None, None)
            await ctx.send("Global regional formatting will now be based on bot's locale.")
            return

        try:
            locale = BabelLocale.parse(language_code, sep="-")
        except (ValueError, UnknownLocaleError):
            await ctx.send("Invalid language code. Use format: `en-US`")
            return
        if locale.territory is None:
            await ctx.send("Invalid format - language code has to include country code, e.g. `en-US`")
            return
        standardized_locale_name = f"{locale.language}-{locale.territory}"
        i18n.set_regional_format(standardized_locale_name)
        await self.bot._i18n_cache.set_regional_format(None, standardized_locale_name)
        await ctx.send(f"Global regional formatting will now be based on `{standardized_locale_name}` locale.")

    @_set.command(aliases=["region"])
    @checks.guildowner_or_permissions(manage_guild=True)
    async def regionalformat(self, ctx: commands.Context, language_code: str = None):
        """Changes the bot's regional format in this server. This is used for
        formatting date, time and numbers.

        `language_code` can be any language code with country code included, e.g. `en-US`, `de-DE`, `fr-FR`, `pl-PL`, etc.
        Leave `language_code` empty to base regional formatting on bot's locale in this server.

        **Examples:**
            - `;set regionalformat en-US`
            - `;set region de-DE`
            - `;set regionalformat` - Resets to the locale.

        **Arguments:**
            - `[language_code]` - The region format to use for the bot in this server.

        """
        if language_code is None:
            i18n.set_contextual_regional_format(None)
            await self.bot._i18n_cache.set_regional_format(ctx.guild, None)
            await ctx.send("Regional formatting will now be based on bot's locale in this server.")
            return

        try:
            locale = BabelLocale.parse(language_code, sep="-")
        except (ValueError, UnknownLocaleError):
            await ctx.send("Invalid language code. Use format: `en-US`")
            return
        if locale.territory is None:
            await ctx.send("Invalid format - language code has to include country code, e.g. `en-US`")
            return
        standardized_locale_name = f"{locale.language}-{locale.territory}"
        i18n.set_contextual_regional_format(standardized_locale_name)
        await self.bot._i18n_cache.set_regional_format(ctx.guild, standardized_locale_name)
        await ctx.send(f"Regional formatting will now be based on `{standardized_locale_name}` locale.")

    @_set.command()
    @checks.is_owner()
    async def custominfo(self, ctx: commands.Context, *, text: str = None):
        """Customizes a section of `;info`.

        The maximum amount of allowed characters is 1024.
        Supports markdown, links and "mentions".

        Link example: `[My link](https://example.com)`

        **Examples:**
            - `;set custominfo >>> I can use **markdown** such as quotes, ||spoilers|| and multiple lines.`
            - `;set custominfo Join my [support server](discord.gg/discord)!`
            - `;set custominfo` - Removes custom info text.

        **Arguments:**
            - `[text]` - The custom info text.

        """
        if not text:
            await ctx.bot._config.custom_info.clear()
            await ctx.send("The custom text has been cleared.")
            return
        if len(text) <= 1024:
            await ctx.bot._config.custom_info.set(text)
            await ctx.send("The custom text has been set.")
            await ctx.invoke(self.info)
        else:
            await ctx.send("Text must be fewer than 1024 characters long.")

    @_set.group(invoke_without_command=True)
    @checks.is_owner()
    async def api(self, ctx: commands.Context, service: str, *, tokens: TokenConverter):
        """Commands to set, list or remove various external API tokens.

        This setting will be asked for by some 3rd party cogs and some core cogs.

        To add the keys provide the service name and the tokens as a comma separated
        list of key,values as described by the cog requesting this command.

        Note: API tokens are sensitive, so this command should only be used in a private channel or in DM with the bot.

        **Examples:**
            - `;set api Spotify redirect_uri localhost`
            - `;set api github client_id,whoops client_secret,whoops`

        **Arguments:**
            - `<service>` - The service you're adding tokens to.
            - `<tokens>` - Pairs of token keys and values. The key and value should be separated by one of ` `, `,`, or `;`.

        """
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            await ctx.message.delete()
        await ctx.bot.set_shared_api_tokens(service, **tokens)
        await ctx.send(f"`{service}` API tokens have been set.")

    @api.command(name="list")
    async def api_list(self, ctx: commands.Context):
        """Show all external API services along with their keys that have been
        set.

        Secrets are not shown.

        **Example:**
            - `;set api list``

        """
        services: dict = await ctx.bot.get_shared_api_tokens()
        if not services:
            await ctx.send("No API services have been set yet.")
            return

        sorted_services = sorted(services.keys(), key=str.lower)

        joined = "Set API services:\n" if len(services) > 1 else ("Set API service:\n")
        for service_name in sorted_services:
            joined += f"+ {service_name}\n"
            for key_name in services[service_name].keys():
                joined += f"  - {key_name}\n"
        for page in pagify(joined, ["\n"], shorten_by=16):
            await ctx.send(box(page.lstrip(" "), lang="diff"))

    @api.command(name="remove", require_var_positional=True)
    async def api_remove(self, ctx: commands.Context, *services: str):
        """Remove the given services with all their keys and tokens.

        **Examples:**
            - `;set api remove Spotify`
            - `;set api remove github audiodb`

        **Arguments:**
            - `<services...>` - The services to remove.

        """
        bot_services = (await ctx.bot.get_shared_api_tokens()).keys()
        if services := [s for s in services if s in bot_services]:
            await self.bot.remove_shared_api_services(*services)
            if len(services) > 1:
                msg = f"Services deleted successfully:\n{humanize_list(services)}"
            else:
                msg = f"Service deleted successfully: {services[0]}"
            await ctx.send(msg)
        else:
            await ctx.send("None of the services you provided had any keys set.")

    @commands.group()
    @checks.is_owner()
    async def helpset(self, ctx: commands.Context):
        """Commands to manage settings for the help command.

        All help settings are applied globally.

        """

    @helpset.command(name="showsettings")
    async def helpset_showsettings(self, ctx: commands.Context):
        """Show the current help settings.

        Warning: These settings may not be accurate if the default formatter is not in use.

        **Example:**
            - `;helpset showsettings``

        """
        help_settings = await commands.help.HelpSettings.from_context(ctx)

        if isinstance(ctx.bot._help_formatter, commands.help.RedHelpFormatter):
            message = help_settings.pretty
        else:
            message = "Warning: The default formatter is not in use, these settings may not apply."
            message += f"\n\n{help_settings.pretty}"

        for page in pagify(message):
            await ctx.send(page)

    @helpset.command(name="resetformatter")
    async def helpset_resetformatter(self, ctx: commands.Context):
        """This resets [botname]'s help formatter to the default formatter.

        **Example:**
            - `;helpset resetformatter``

        """
        ctx.bot.reset_help_formatter()
        await ctx.send(
            "The help formatter has been reset. This will not prevent cogs from modifying help, you may need to remove a cog if this has been an issue.",
        )

    @helpset.command(name="resetsettings")
    async def helpset_resetsettings(self, ctx: commands.Context):
        """This resets [botname]'s help settings to their defaults.

        This may not have an impact when using custom formatters from 3rd party cogs

        **Example:**
            - `;helpset resetsettings``

        """
        await ctx.bot._config.help.clear()
        await ctx.send("The help settings have been reset to their defaults. This may not have an impact when using 3rd party help formatters.")

    @helpset.command(name="usemenus")
    async def helpset_usemenus(self, ctx: commands.Context, use_menus: bool = None):
        """Allows the help command to be sent as a paginated menu instead of
        separate messages.

        When enabled, `;help` will only show one page at a time and will use reactions to navigate between pages.

        This defaults to False.
        Using this without a setting will toggle.

         **Examples:**
            - `;helpset usemenus True` - Enables using menus.
            - `;helpset usemenus` - Toggles the value.

        **Arguments:**
            - `[use_menus]` - Whether to use menus. Leave blank to toggle.

        """
        if use_menus is None:
            use_menus = not await ctx.bot._config.help.use_menus()
        await ctx.bot._config.help.use_menus.set(use_menus)
        if use_menus:
            await ctx.send("Help will use menus.")
        else:
            await ctx.send("Help will not use menus.")

    @helpset.command(name="showhidden")
    async def helpset_showhidden(self, ctx: commands.Context, show_hidden: bool = None):
        """This allows the help command to show hidden commands.

        This defaults to False.
        Using this without a setting will toggle.

        **Examples:**
            - `;helpset showhidden True` - Enables showing hidden commands.
            - `;helpset showhidden` - Toggles the value.

        **Arguments:**
            - `[show_hidden]` - Whether to use show hidden commands in help. Leave blank to toggle.

        """
        if show_hidden is None:
            show_hidden = not await ctx.bot._config.help.show_hidden()
        await ctx.bot._config.help.show_hidden.set(show_hidden)
        if show_hidden:
            await ctx.send("Help will not filter hidden commands.")
        else:
            await ctx.send("Help will filter hidden commands.")

    @helpset.command(name="showaliases")
    async def helpset_showaliases(self, ctx: commands.Context, show_aliases: bool = None):
        """This allows the help command to show existing commands aliases if there
        is any.

        This defaults to True.
        Using this without a setting will toggle.

        **Examples:**
            - `;helpset showaliases False` - Disables showing aliases on this server.
            - `;helpset showaliases` - Toggles the value.

        **Arguments:**
            - `[show_aliases]` - Whether to include aliases in help. Leave blank to toggle.

        """
        if show_aliases is None:
            show_aliases = not await ctx.bot._config.help.show_aliases()
        await ctx.bot._config.help.show_aliases.set(show_aliases)
        if show_aliases:
            await ctx.send("Help will now show command aliases.")
        else:
            await ctx.send("Help will no longer show command aliases.")

    @helpset.command(name="usetick")
    async def helpset_usetick(self, ctx: commands.Context, use_tick: bool = None):
        """This allows the help command message to be ticked if help is sent to a
        DM.

        Ticking is reacting to the help message with a ✅.

        Defaults to False.
        Using this without a setting will toggle.

        Note: This is only used when the bot is not using menus.

        **Examples:**
            - `;helpset usetick False` - Disables ticking when help is sent to DMs.
            - `;helpset usetick` - Toggles the value.

        **Arguments:**
            - `[use_tick]` - Whether to tick the help command when help is sent to DMs. Leave blank to toggle.

        """
        if use_tick is None:
            use_tick = not await ctx.bot._config.help.use_tick()
        await ctx.bot._config.help.use_tick.set(use_tick)
        if use_tick:
            await ctx.send("Help will now tick the command when sent in a DM.")
        else:
            await ctx.send("Help will not tick the command when sent in a DM.")

    @helpset.command(name="verifychecks")
    async def helpset_permfilter(self, ctx: commands.Context, verify: bool = None):
        """Sets if commands which can't be run in the current context should be
        filtered from help.

        Defaults to True.
        Using this without a setting will toggle.

        **Examples:**
            - `;helpset verifychecks False` - Enables showing unusable commands in help.
            - `;helpset verifychecks` - Toggles the value.

        **Arguments:**
            - `[verify]` - Whether to hide unusable commands in help. Leave blank to toggle.

        """
        if verify is None:
            verify = not await ctx.bot._config.help.verify_checks()
        await ctx.bot._config.help.verify_checks.set(verify)
        if verify:
            await ctx.send("Help will only show for commands which can be run.")
        else:
            await ctx.send("Help will show up without checking if the commands can be run.")

    @helpset.command(name="verifyexists")
    async def helpset_verifyexists(self, ctx: commands.Context, verify: bool = None):
        """Sets whether the bot should respond to help commands for nonexistent
        topics.

        When enabled, this will indicate the existence of help topics, even if the user can't use it.

        Note: This setting on its own does not fully prevent command enumeration.

        Defaults to False.
        Using this without a setting will toggle.

        **Examples:**
            - `;helpset verifyexists True` - Enables sending help for nonexistent topics.
            - `;helpset verifyexists` - Toggles the value.

        **Arguments:**
            - `[verify]` - Whether to respond to help for nonexistent topics. Leave blank to toggle.

        """
        if verify is None:
            verify = not await ctx.bot._config.help.verify_exists()
        await ctx.bot._config.help.verify_exists.set(verify)
        if verify:
            await ctx.send("Help will verify the existence of help topics.")
        else:
            await ctx.send("Help will only verify the existence of help topics via fuzzy help (if enabled).")

    @helpset.command(name="pagecharlimit")
    async def helpset_pagecharlimt(self, ctx: commands.Context, limit: int):
        """Set the character limit for each page in the help message.

        Note: This setting only applies to embedded help.

        The default value is 1000 characters. The minimum value is 500.
        The maximum is based on the lower of what you provide and what discord allows.

        Please note that setting a relatively small character limit may
        mean some pages will exceed this limit.

        **Example:**
            - `;helpset pagecharlimit 1500`

        **Arguments:**
            - `<limit>` - The max amount of characters to show per page in the help message.

        """
        if limit < 500:
            await ctx.send("You must give a value of at least 500 characters.")
            return

        await ctx.bot._config.help.page_char_limit.set(limit)
        await ctx.send(f"Done. The character limit per page has been set to {limit}.")

    @helpset.command(name="maxpages")
    async def helpset_maxpages(self, ctx: commands.Context, pages: int):
        """Set the maximum number of help pages sent in a server channel.

        Note: This setting does not apply to menu help.

        If a help message contains more pages than this value, the help message will
        be sent to the command author via DM. This is to help reduce spam in server
        text channels.

        The default value is 2 pages.

        **Examples:**
            - `;helpset maxpages 50` - Basically never send help to DMs.
            - `;helpset maxpages 0` - Always send help to DMs.

        **Arguments:**
            - `<limit>` - The max pages allowed to send per help in a server.

        """
        if pages < 0:
            await ctx.send("You must give a value of zero or greater!")
            return

        await ctx.bot._config.help.max_pages_in_guild.set(pages)
        await ctx.send(f"Done. The page limit has been set to {pages}.")

    @helpset.command(name="deletedelay")
    async def helpset_deletedelay(self, ctx: commands.Context, seconds: int):
        """Set the delay after which help pages will be deleted.

        The setting is disabled by default, and only applies to non-menu help,
        sent in server text channels.
        Setting the delay to 0 disables this feature.

        The bot has to have MANAGE_MESSAGES permission for this to work.

        **Examples:**
            - `;helpset deletedelay 60` - Delete the help pages after a minute.
            - `;helpset deletedelay 1` - Delete the help pages as quickly as possible.
            - `;helpset deletedelay 1209600` - Max time to wait before deleting (14 days).
            - `;helpset deletedelay 0` - Disable deleting help pages.

        **Arguments:**
            - `<seconds>` - The seconds to wait before deleting help pages.

        """
        if seconds < 0:
            await ctx.send("You must give a value of zero or greater!")
            return
        if seconds > 60**2 * 24 * 14:  # 14 days
            await ctx.send("The delay cannot be longer than 14 days!")
            return

        await ctx.bot._config.help.delete_delay.set(seconds)
        if seconds == 0:
            await ctx.send("Done. Help messages will not be deleted now.")
        else:
            await ctx.send(f"Done. The delete delay has been set to {seconds} seconds.")

    @helpset.command(name="reacttimeout")
    async def helpset_reacttimeout(self, ctx: commands.Context, seconds: int):
        """Set the timeout for reactions, if menus are enabled.

        The default is 30 seconds.
        The timeout has to be between 15 and 300 seconds.

        **Examples:**
            - `;helpset reacttimeout 30` - The default timeout.
            - `;helpset reacttimeout 60` - Timeout of 1 minute.
            - `;helpset reacttimeout 15` - Minimum allowed timeout.
            - `;helpset reacttimeout 300` - Max allowed timeout (5 mins).

        **Arguments:**
            - `<seconds>` - The timeout, in seconds, of the reactions.

        """
        if seconds < 15:
            await ctx.send("You must give a value of at least 15 seconds!")
            return
        if seconds > 300:
            await ctx.send("The timeout cannot be greater than 5 minutes!")
            return

        await ctx.bot._config.help.react_timeout.set(seconds)
        await ctx.send(f"Done. The reaction timeout has been set to {seconds} seconds.")

    @helpset.command(name="tagline")
    async def helpset_tagline(self, ctx: commands.Context, *, tagline: str = None):
        """Set the tagline to be used.

        The maximum tagline length is 2048 characters.
        This setting only applies to embedded help. If no tagline is specified, the default will be used instead.

        **Examples:**
            - `;helpset tagline Thanks for using the bot!`
            - `;helpset tagline` - Resets the tagline to the default.

        **Arguments:**
            - `[tagline]` - The tagline to appear at the bottom of help embeds. Leave blank to reset.

        """
        if tagline is None:
            await ctx.bot._config.help.tagline.set("")
            return await ctx.send("The tagline has been reset.")

        if len(tagline) > 2048:
            await ctx.send("Your tagline is too long! Please shorten it to be no more than 2048 characters long.")
            return

        await ctx.bot._config.help.tagline.set(tagline)
        await ctx.send("The tagline has been set.")

    @commands.command(cooldown_after_parsing=True)
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def contact(self, ctx: commands.Context, *, message: str):
        """Sends a message to the owner.

        This is limited to one message every 60 seconds per person.

        **Example:**
            - `;contact Help! The bot has become sentient!`

        **Arguments:**
            - `[message]` - The message to send to the owner.

        """
        guild = ctx.message.guild
        author = ctx.message.author
        footer = f"User ID: {author.id}"

        if ctx.guild is None:
            source = "through DM"
        else:
            source = f"from {guild}"
            footer += f" | Server ID: {guild.id}"

        prefixes = await ctx.bot.get_valid_prefixes()
        prefix = re.sub(rf"<@!?{ctx.me.id}>", f"@{ctx.me.name}".replace("\\", r"\\"), prefixes[0])

        content = f"Use `{prefix}dm {author.id} <text>` to reply to this user"

        description = f"Sent by {author} {source}"

        destinations = await ctx.bot.get_owner_notification_destinations()

        if not destinations:
            await ctx.send("I've been configured not to send this anywhere.")
            return

        successful = False

        for destination in destinations:
            is_dm = isinstance(destination, discord.User)
            send_embed = None

            if is_dm:
                send_embed = await ctx.bot._config.user(destination).embeds()
            else:
                if not destination.permissions_for(destination.guild.me).send_messages:
                    continue
                if destination.permissions_for(destination.guild.me).embed_links:
                    send_embed = await ctx.bot._config.channel(destination).embeds()
                    if send_embed is None:
                        send_embed = await ctx.bot._config.guild(destination.guild).embeds()
                else:
                    send_embed = False

            if send_embed is None:
                send_embed = await ctx.bot._config.embeds()

            if send_embed:
                color = ctx.bot._color if is_dm else await ctx.bot.get_embed_color(destination)
                e = discord.Embed(colour=color, description=message)
                if author.avatar_url:
                    e.set_author(name=description, icon_url=author.avatar_url)
                else:
                    e.set_author(name=description)

                e.set_footer(text=footer)

                try:
                    await destination.send(embed=e)
                except discord.Forbidden:
                    logger.exception(f"Contact failed to {destination}({destination.id})")
                    # Should this automatically opt them out?
                except discord.HTTPException:
                    logger.exception(f"An unexpected error happened while attempting to send contact to {destination}({destination.id})")
                else:
                    successful = True

            else:
                msg_text = f"{description}\nMessage:\n\n{message}\n{footer}"

                try:
                    await destination.send(f"{content}\n{box(msg_text)}")
                except discord.Forbidden:
                    logger.exception(f"Contact failed to {destination}({destination.id})")
                    # Should this automatically opt them out?
                except discord.HTTPException:
                    logger.exception(f"An unexpected error happened while attempting to send contact to {destination}({destination.id})")
                else:
                    successful = True

        if successful:
            await ctx.send("Your message has been sent.")
        else:
            await ctx.send("I'm unable to deliver your message. Sorry.")

    @commands.command(hidden=True)
    @checks.is_owner()
    async def dm(self, ctx: commands.Context, user_id: int, *, message: str):
        """Sends a DM to a user.

        This command needs a user ID to work.

        To get a user ID, go to Discord's settings and open the 'Appearance' tab.
        Enable 'Developer Mode', then right click a user and click on 'Copy ID'.

        **Example:**
            - `;dm 262626262626262626 Do you like me? Yes / No`

        **Arguments:**
            - `[message]` - The message to dm to the user.

        """
        destination = self.bot.get_user(user_id)
        if destination is None or destination.bot:
            await ctx.send("Invalid ID, user not found, or user is a bot. You can only send messages to people I share a server with.")
            return

        prefixes = await ctx.bot.get_valid_prefixes()
        prefix = re.sub(rf"<@!?{ctx.me.id}>", f"@{ctx.me.name}".replace("\\", r"\\"), prefixes[0])
        description = f"Owner of {ctx.bot.user}"
        content = f"You can reply to this message with {prefix}contact"
        if await ctx.embed_requested():
            e = discord.Embed(colour=discord.Colour.blurple(), description=message)

            e.set_footer(text=content)
            if ctx.bot.user.avatar_url:
                e.set_author(name=description, icon_url=ctx.bot.user.avatar_url)
            else:
                e.set_author(name=description)

            try:
                await destination.send(embed=e)
            except discord.HTTPException:
                await ctx.send(f"Sorry, I couldn't deliver your message to {destination}")
            else:
                await ctx.send(f"Message delivered to {destination}")
        else:
            response = f"{description}\nMessage:\n\n{message}"
            try:
                await destination.send(f"{box(response)}\n{content}")
            except discord.HTTPException:
                await ctx.send(f"Sorry, I couldn't deliver your message to {destination}")
            else:
                await ctx.send(f"Message delivered to {destination}")

    @commands.command(hidden=True)
    @checks.is_owner()
    async def datapath(self, ctx: commands.Context):
        """Prints the bot's data path."""
        from melaniebot.core.data_manager import basic_config

        data_dir = Path(basic_config["DATA_PATH"])
        msg = f"Data path: {data_dir}"
        await ctx.send(box(msg))

    @commands.command(hidden=True)
    @checks.is_owner()
    async def debuginfo(self, ctx: commands.Context):
        """Shows debug information useful for debugging."""
        IS_WINDOWS = os.name == "nt"
        IS_MAC = sys.platform == "darwin"
        IS_LINUX = sys.platform == "linux"

        python_version = ".".join(map(str, sys.version_info[:3]))
        pyver = f"{python_version} ({platform.architecture()[0]})"
        pipver = pip.__version__
        redver = red_version_info
        dpy_version = discord.__version__
        if IS_WINDOWS:
            os_info = platform.uname()
            osver = f"{os_info.system} {os_info.release} (version {os_info.version})"
        elif IS_MAC:
            os_info = platform.mac_ver()
            osver = f"Mac OSX {os_info[0]} {os_info[2]}"
        elif IS_LINUX:
            os_info = distro.linux_distribution()
            osver = f"{os_info[0]} {os_info[1]}".strip()
        else:
            osver = "Could not parse OS, report this on Github."
        user_who_ran = getpass.getuser()
        driver = storage_type()

        from melaniebot.core.data_manager import basic_config, config_file

        data_path = Path(basic_config["DATA_PATH"])
        disabled_intents = ", ".join(intent_name.replace("_", " ").title() for intent_name, enabled in self.bot.intents if not enabled) or "None"

        def _datasize(num: int):
            for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
                if abs(num) < 1024.0:
                    return f"{num:.1f}{unit}"
                num /= 1024.0
            return f"{num:.1f}{'YB'}"

        memory_ram = psutil.virtual_memory()
        ram_string = f"{_datasize(memory_ram.used)}/{_datasize(memory_ram.total)} ({memory_ram.percent}%)"

        owners = []
        for uid in self.bot.owner_ids:
            try:
                u = await self.bot.get_or_fetch_user(uid)
                owners.append(f"{u.id} ({u})")
            except discord.HTTPException:
                owners.append(f"{uid} (Unresolvable)")
        owners_string = ", ".join(owners) or "None"

        resp_intro = "# Debug Info for Melanie:"
        resp_system_intro = "## System Metadata:"
        resp_system = f"CPU Cores: {psutil.cpu_count()} ({platform.machine()})\nRAM: {ram_string}\n"
        resp_os_intro = "## OS Variables:"
        resp_os = f"OS version: {osver}\nUser: {user_who_ran}\n"  # Ran where off to?!
        resp_py_metadata = f"Python executable: {sys.executable}\nPython version: {pyver}\nPip version: {pipver}\n"
        resp_red_metadata = f"Melanie version: {redver}\nDiscord.py version: {dpy_version}\n"
        resp_red_vars_intro = "## Melanie variables:"
        resp_red_vars = f"Instance name: {data_manager.instance_name}\nOwner(s): {owners_string}\nStorage type: {driver}\nDisabled intents: {disabled_intents}\nData path: {data_path}\nMetadata file: {config_file}"

        response = (
            box(resp_intro, lang="md"),
            "\n",
            box(resp_system_intro, lang="md"),
            box(resp_system),
            "\n",
            box(resp_os_intro, lang="md"),
            box(resp_os),
            box(resp_py_metadata),
            box(resp_red_metadata),
            "\n",
            box(resp_red_vars_intro, lang="md"),
            box(resp_red_vars),
        )

        await ctx.send("".join(response))

    # You may ask why this command is owner-only,
    # cause after all it could be quite useful to guild owners!
    # Truth to be told, that would require us to make some part of this
    # more end-user friendly rather than just bot owner friendly - terms like
    # 'global call once checks' are not of any use to someone who isn't bot owner.
    @commands.is_owner()
    @commands.command(hidden=True)
    async def diagnoseissues(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel],
        member: Union[discord.Member, discord.User],
        *,
        command_name: str,
    ) -> None:
        """Diagnose issues with the command checks with ease!.

        If you want to diagnose the command from a text channel in a different server,
        you can do so by using the command in DMs.

        **Example:**
            - `;diagnoseissues #general @Slime ban` - Diagnose why @Slime can't use `;ban` in #general channel.

        **Arguments:**
            - `[channel]` - The text channel that the command should be tested for. Defaults to the current channel.
            - `<member>` - The member that should be considered as the command caller.
            - `<command_name>` - The name of the command to test.

        """
        if channel is None:
            channel = ctx.channel
            if not isinstance(channel, discord.TextChannel):
                await ctx.send("The channel needs to be passed when using this command in DMs.")
                return

        command = self.bot.get_command(command_name)
        if command is None:
            await ctx.send("Command not found!")
            return

        # This is done to allow the bot owner to diagnose a command
        # while not being a part of the server.
        if isinstance(member, discord.User):
            maybe_member = channel.guild.get_member(member.id)
            if maybe_member is None:
                await ctx.send("The given user is not a member of the diagnosed server.")
                return
            member = maybe_member

        if not channel.permissions_for(member).send_messages:
            # Let's make Flame happy here
            await ctx.send(f"Don't try to fool me, the given member can't access the {channel.mention} channel!")
            return
        issue_diagnoser = IssueDiagnoser(self.bot, ctx, channel, member, command)
        await ctx.send(await issue_diagnoser.diagnose())

    @commands.group(aliases=["whitelist"])
    @checks.is_owner()
    async def allowlist(self, ctx: commands.Context):
        """Commands to manage the allowlist.

        Warning: When the allowlist is in use, the bot will ignore commands from everyone not on the list.

        Use `;allowlist clear` to disable the allowlist

        """

    @allowlist.command(name="add", require_var_positional=True)
    async def allowlist_add(self, ctx: commands.Context, *users: Union[discord.Member, int]):
        """Adds users to the allowlist.

        **Examples:**
            - `;allowlist add @26 @Will` - Adds two users to the allowlist.
            - `;allowlist add 262626262626262626` - Adds a user by ID.

        **Arguments:**
            - `<users...>` - The user or users to add to the allowlist.

        """
        await self.bot.add_to_whitelist(users)
        if len(users) > 1:
            await ctx.send("Users have been added to the allowlist.")
        else:
            await ctx.send("User has been added to the allowlist.")

    @allowlist.command(name="list")
    async def allowlist_list(self, ctx: commands.Context):
        """Lists users on the allowlist.

        **Example:**
            - `;allowlist list`

        """
        curr_list = await ctx.bot._config.whitelist()

        if not curr_list:
            await ctx.send("Allowlist is empty.")
            return
        if len(curr_list) > 1:
            msg = "Users on the allowlist:"
        else:
            msg = "User on the allowlist:"
        for user_id in curr_list:
            user = self.bot.get_user(user_id) or "Unknown or Deleted User"
            msg += f"\n\t- {user_id} ({user})"

        for page in pagify(msg):
            await ctx.send(box(page))

    @allowlist.command(name="remove", require_var_positional=True)
    async def allowlist_remove(self, ctx: commands.Context, *users: Union[discord.Member, int]):
        """Removes users from the allowlist.

        The allowlist will be disabled if all users are removed.

        **Examples:**
            - `;allowlist remove @26 @Will` - Removes two users from the allowlist.
            - `;allowlist remove 262626262626262626` - Removes a user by ID.

        **Arguments:**
            - `<users...>` - The user or users to remove from the allowlist.

        """
        await self.bot.remove_from_whitelist(users)
        if len(users) > 1:
            await ctx.send("Users have been removed from the allowlist.")
        else:
            await ctx.send("User has been removed from the allowlist.")

    @allowlist.command(name="clear")
    async def allowlist_clear(self, ctx: commands.Context):
        """Clears the allowlist.

        This disables the allowlist.

        **Example:**
            - `;allowlist clear`

        """
        await self.bot.clear_whitelist()
        await ctx.send("Allowlist has been cleared.")

    @commands.group(aliases=["blacklist", "denylist"])
    @checks.is_owner()
    async def blocklist(self, ctx: commands.Context):
        """Commands to manage the blocklist.

        Use `;blocklist clear` to disable the blocklist

        """

    @blocklist.command(name="add", require_var_positional=True)
    async def blocklist_add(self, ctx: commands.Context, *users: Union[discord.Member, int]):
        """Adds users to the blocklist.

        **Examples:**
            - `;blocklist add @26 @Will` - Adds two users to the blocklist.
            - `;blocklist add 262626262626262626` - Blocks a user by ID.

        **Arguments:**
            - `<users...>` - The user or users to add to the blocklist.

        """
        for user in users:
            user_obj = discord.Object(id=user) if isinstance(user, int) else user
            if await ctx.bot.is_owner(user_obj):
                await ctx.send("You cannot add an owner to the blocklist!")
                return

        await self.bot.add_to_blacklist(users)
        if len(users) > 1:
            await ctx.send("Users have been added to the blocklist.")
        else:
            await ctx.send("User has been added to the blocklist. 💅🏿")

    @blocklist.command(name="list")
    async def blocklist_list(self, ctx: commands.Context):
        """Lists users on the blocklist.

        **Example:**
            - `;blocklist list`

        """
        curr_list = await self.bot.get_blacklist()

        if not curr_list:
            await ctx.send("Blocklist is empty.")
            return
        if len(curr_list) > 1:
            msg = "Users on the blocklist:"
        else:
            msg = "User on the blocklist:"
        for user_id in curr_list:
            user = self.bot.get_user(user_id) or "Unknown or Deleted User"
            msg += f"\n\t- {user_id} ({user})"

        for page in pagify(msg):
            await ctx.send(box(page))

    @blocklist.command(name="remove", require_var_positional=True)
    async def blocklist_remove(self, ctx: commands.Context, *users: Union[discord.Member, int]):
        """Removes users from the blocklist.

        **Examples:**
            - `;blocklist remove @26 @Will` - Removes two users from the blocklist.
            - `;blocklist remove 262626262626262626` - Removes a user by ID.

        **Arguments:**
            - `<users...>` - The user or users to remove from the blocklist.

        """
        await self.bot.remove_from_blacklist(users)
        if len(users) > 1:
            await ctx.send("Users have been removed from the blocklist.")
        else:
            await ctx.send("User has been removed from the blocklist.")

    @blocklist.command(name="clear")
    async def blocklist_clear(self, ctx: commands.Context):
        """Clears the blocklist.

        **Example:**
            - `;blocklist clear`

        """
        await self.bot.clear_blacklist()
        await ctx.send("Blocklist has been cleared.")

    @checks.is_owner()
    @commands.group(aliases=["localwhitelist"], hiden=True)
    @commands.guild_only()
    async def localallowlist(self, ctx: commands.Context):
        """Commands to manage the server specific allowlist.

        Warning: When the allowlist is in use, the bot will ignore commands from everyone not on the list in the server.

        Use `;localallowlist clear` to disable the allowlist

        """

    @localallowlist.command(name="add", require_var_positional=True, hidden=True)
    async def localallowlist_add(self, ctx: commands.Context, *users_or_roles: Union[discord.Member, discord.Role, int]):
        """Adds a user or role to the server allowlist.

        **Examples:**
            - `;localallowlist add @26 @Will` - Adds two users to the local allowlist.
            - `;localallowlist add 262626262626262626` - Allows a user by ID.
            - `;localallowlist add "Super Admins"` - Allows a role with a space in the name without mentioning.

        **Arguments:**
            - `<users_or_roles...>` - The users or roles to remove from the local allowlist.

        """
        [getattr(u_or_r, "name", u_or_r) for u_or_r in users_or_roles]
        uids = {getattr(u_or_r, "id", u_or_r) for u_or_r in users_or_roles}
        if not (ctx.guild.owner == ctx.author or await self.bot.is_owner(ctx.author)):
            current_whitelist = await self.bot.get_whitelist(ctx.guild)
            theoretical_whitelist = current_whitelist.union(uids)
            ids = {ctx.author.id, *(getattr(ctx.author, "_roles", []))}
            if ids.isdisjoint(theoretical_whitelist):
                return await ctx.send(
                    "I cannot allow you to do this, as it would remove your ability to run commands, please ensure to add yourself to the allowlist first.",
                )
        await self.bot.add_to_whitelist(uids, guild=ctx.guild)

        if len(uids) > 1:
            await ctx.send("Users and/or roles have been added to the allowlist.")
        else:
            await ctx.send("User or role has been added to the allowlist.")

    @localallowlist.command(name="list")
    async def localallowlist_list(self, ctx: commands.Context):
        """Lists users and roles on the server allowlist.

        **Example:**
            - `;localallowlist list`

        """
        curr_list = await self.bot.get_whitelist(ctx.guild)

        if not curr_list:
            await ctx.send("Server allowlist is empty.")
            return
        if len(curr_list) > 1:
            msg = "Allowed users and/or roles:"
        else:
            msg = "Allowed user or role:"
        for obj_id in curr_list:
            user_or_role = self.bot.get_user(obj_id) or ctx.guild.get_role(obj_id) or "Unknown or Deleted User/Role"
            msg += f"\n\t- {obj_id} ({user_or_role})"

        for page in pagify(msg):
            await ctx.send(box(page))

    @localallowlist.command(name="remove", require_var_positional=True)
    async def localallowlist_remove(self, ctx: commands.Context, *users_or_roles: Union[discord.Member, discord.Role, int]):
        """Removes user or role from the allowlist.

        The local allowlist will be disabled if all users are removed.

        **Examples:**
            - `;localallowlist remove @26 @Will` - Removes two users from the local allowlist.
            - `;localallowlist remove 262626262626262626` - Removes a user by ID.
            - `;localallowlist remove "Super Admins"` - Removes a role with a space in the name without mentioning.

        **Arguments:**
            - `<users_or_roles...>` - The users or roles to remove from the local allowlist.

        """
        [getattr(u_or_r, "name", u_or_r) for u_or_r in users_or_roles]
        uids = {getattr(u_or_r, "id", u_or_r) for u_or_r in users_or_roles}
        if not (ctx.guild.owner == ctx.author or await self.bot.is_owner(ctx.author)):
            current_whitelist = await self.bot.get_whitelist(ctx.guild)
            theoretical_whitelist = current_whitelist - uids
            ids = {ctx.author.id, *(getattr(ctx.author, "_roles", []))}
            if theoretical_whitelist and ids.isdisjoint(theoretical_whitelist):
                return await ctx.send("I cannot allow you to do this, as it would remove your ability to run commands.")
        await self.bot.remove_from_whitelist(uids, guild=ctx.guild)

        if len(uids) > 1:
            await ctx.send("Users and/or roles have been removed from the server allowlist.")
        else:
            await ctx.send("User or role has been removed from the server allowlist.")

    @localallowlist.command(name="clear")
    async def localallowlist_clear(self, ctx: commands.Context):
        """Clears the allowlist.

        This disables the local allowlist and clears all entries.

        **Example:**
            - `;localallowlist clear`

        """
        await self.bot.clear_whitelist(ctx.guild)
        await ctx.send("Server allowlist has been cleared.")

    @commands.group(aliases=["localblacklist"], hidden=True)
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def localblocklist(self, ctx: commands.Context):
        """Commands to manage the server specific blocklist.

        Use `;localblocklist clear` to disable the blocklist

        """

    @localblocklist.command(name="add", require_var_positional=True)
    async def localblocklist_add(self, ctx: commands.Context, *users_or_roles: Union[discord.Member, discord.Role, int]):
        """Adds a user or role to the local blocklist.

        **Examples:**
            - `;localblocklist add @26 @Will` - Adds two users to the local blocklist.
            - `;localblocklist add 262626262626262626` - Blocks a user by ID.
            - `;localblocklist add "Bad Apples"` - Blocks a role with a space in the name without mentioning.

        **Arguments:**
            - `<users_or_roles...>` - The users or roles to add to the local blocklist.

        """
        for user_or_role in users_or_roles:
            uid = discord.Object(id=getattr(user_or_role, "id", user_or_role))
            if uid.id == ctx.author.id:
                await ctx.send("You cannot add yourself to the blocklist!")
                return
            if uid.id == ctx.guild.owner_id and not await ctx.bot.is_owner(ctx.author):
                await ctx.send("You cannot add the guild owner to the blocklist!")
                return
            if await ctx.bot.is_owner(uid):
                await ctx.send("You cannot add a bot owner to the blocklist!")
                return
        await self.bot.add_to_blacklist(users_or_roles, guild=ctx.guild)

        if len(users_or_roles) > 1:
            await ctx.send("Users and/or roles have been added from the server blocklist.")
        else:
            await ctx.send("User or role has been added from the server blocklist.")

    @localblocklist.command(name="list")
    async def localblocklist_list(self, ctx: commands.Context):
        """Lists users and roles on the server blocklist.

        **Example:**
            - `;localblocklist list`

        """
        curr_list = await self.bot.get_blacklist(ctx.guild)

        if not curr_list:
            await ctx.send("Server blocklist is empty.")
            return
        if len(curr_list) > 1:
            msg = "Blocked users and/or roles:"
        else:
            msg = "Blocked user or role:"
        for obj_id in curr_list:
            user_or_role = self.bot.get_user(obj_id) or ctx.guild.get_role(obj_id) or "Unknown or Deleted User/Role"
            msg += f"\n\t- {obj_id} ({user_or_role})"

        for page in pagify(msg):
            await ctx.send(box(page))

    @localblocklist.command(name="remove", require_var_positional=True)
    async def localblocklist_remove(self, ctx: commands.Context, *users_or_roles: Union[discord.Member, discord.Role, int]):
        """Removes user or role from local blocklist.

        **Examples:**
            - `;localblocklist remove @26 @Will` - Removes two users from the local blocklist.
            - `;localblocklist remove 262626262626262626` - Unblocks a user by ID.
            - `;localblocklist remove "Bad Apples"` - Unblocks a role with a space in the name without mentioning.

        **Arguments:**
            - `<users_or_roles...>` - The users or roles to remove from the local blocklist.

        """
        await self.bot.remove_from_blacklist(users_or_roles, guild=ctx.guild)

        if len(users_or_roles) > 1:
            await ctx.send("Users and/or roles have been removed from the server blocklist.")
        else:
            await ctx.send("User or role has been removed from the server blocklist.")

    @localblocklist.command(name="clear")
    async def localblocklist_clear(self, ctx: commands.Context):
        """Clears the server blocklist.

        This disables the server blocklist and clears all entries.

        **Example:**
            - `;blocklist clear`

        """
        await self.bot.clear_blacklist(ctx.guild)
        await ctx.send("Server blocklist has been cleared.")

    @checks.guildowner_or_permissions(administrator=True)
    @commands.group(name="command")
    async def command_manager(self, ctx: commands.Context):
        """Commands to enable and disable commands and cogs."""

    @checks.is_owner()
    @command_manager.command(name="defaultdisablecog", hidden=True)
    async def command_default_disable_cog(self, ctx: commands.Context, *, cog: CogConverter):
        """Set the default state for a cog as disabled.

        This will disable the cog for all servers by default.
        To override it, use `;command enablecog` on the servers you want to allow usage.

        Note: This will only work on loaded cogs, and must reference the title-case cog name.

        **Examples:**
            - `;command defaultdisablecog Economy`
            - `;command defaultdisablecog ModLog`

        **Arguments:**
            - `<cog>` - The name of the cog to make disabled by default. Must be title-case.

        """
        cogname = cog.qualified_name
        if isinstance(cog, commands.commands._RuleDropper):
            return await ctx.send("You can't disable this cog by default.")
        await self.bot._disabled_cog_cache.default_disable(cogname)
        await ctx.send(f"{cogname} has been set as disabled by default.")

    @checks.is_owner()
    @command_manager.command(name="defaultenablecog", hidden=True)
    async def command_default_enable_cog(self, ctx: commands.Context, *, cog: CogConverter):
        """Set the default state for a cog as enabled.

        This will re-enable the cog for all servers by default.
        To override it, use `;command disablecog` on the servers you want to disallow usage.

        Note: This will only work on loaded cogs, and must reference the title-case cog name.

        **Examples:**
            - `;command defaultenablecog Economy`
            - `;command defaultenablecog ModLog`

        **Arguments:**
            - `<cog>` - The name of the cog to make enabled by default. Must be title-case.

        """
        cogname = cog.qualified_name
        await self.bot._disabled_cog_cache.default_enable(cogname)
        await ctx.send(f"{cogname} has been set as enabled by default.")

    @commands.guild_only()
    @command_manager.command(name="disablecog", hidden=True)
    async def command_disable_cog(self, ctx: commands.Context, *, cog: CogConverter):
        """Disable a cog in this server.

        Note: This will only work on loaded cogs, and must reference the title-case cog name.

        **Examples:**
            - `;command disablecog Economy`
            - `;command disablecog ModLog`

        **Arguments:**
            - `<cog>` - The name of the cog to disable on this server. Must be title-case.

        """
        cogname = cog.qualified_name
        if isinstance(cog, commands.commands._RuleDropper):
            return await ctx.send("You can't disable this cog as you would lock yourself out.")
        if await self.bot._disabled_cog_cache.disable_cog_in_guild(cogname, ctx.guild.id):
            await ctx.send(f"{cogname} has been disabled in this guild.")
        else:
            await ctx.send(f"{cogname} was already disabled (nothing to do).")

    @commands.guild_only()
    @command_manager.command(name="enablecog", usage="<cog>", hidden=True)
    async def command_enable_cog(self, ctx: commands.Context, *, cogname: str):
        """Enable a cog in this server.

        Note: This will only work on loaded cogs, and must reference the title-case cog name.

        **Examples:**
            - `;command enablecog Economy`
            - `;command enablecog ModLog`

        **Arguments:**
            - `<cog>` - The name of the cog to enable on this server. Must be title-case.

        """
        if await self.bot._disabled_cog_cache.enable_cog_in_guild(cogname, ctx.guild.id):
            await ctx.send(f"{cogname} has been enabled in this guild.")
        elif cog := self.bot.get_cog(cogname):
            await ctx.send(f"{cogname} was not disabled (nothing to do).")
        else:
            return await ctx.send(_('Cog "{arg}" not found.').format(arg=cogname))

    @commands.guild_only()
    @command_manager.command(name="listdisabledcogs", hidden=True)
    async def command_list_disabled_cogs(self, ctx: commands.Context):
        """List the cogs which are disabled in this server.

        **Example:**
            - `;command listdisabledcogs`

        """
        disabled = [
            cog.qualified_name for cog in self.bot.cogs.values() if await self.bot._disabled_cog_cache.cog_disabled_in_guild(cog.qualified_name, ctx.guild.id)
        ]
        if disabled:
            output = "The following cogs are disabled in this guild:\n"
            output += humanize_list(disabled)

            for page in pagify(output):
                await ctx.send(page)
        else:
            await ctx.send("There are no disabled cogs in this guild.")

    @command_manager.group(name="listdisabled", invoke_without_command=True)
    async def list_disabled(self, ctx: commands.Context):
        """List disabled commands.

        If you're the bot owner, this will show global disabled commands by default.
        Otherwise, this will show disabled commands on the current server.

        **Example:**
            - `;command listdisabled`

        """
        # Select the scope based on the author's privileges
        if await ctx.bot.is_owner(ctx.author):
            await ctx.invoke(self.list_disabled_global)
        else:
            await ctx.invoke(self.list_disabled_guild)

    @list_disabled.command(name="global")
    async def list_disabled_global(self, ctx: commands.Context):
        """List disabled commands globally.

        **Example:**
            - `;command listdisabled global`

        """
        disabled_list = await self.bot._config.disabled_commands()
        if not disabled_list:
            return await ctx.send("There aren't any globally disabled commands.")

        if len(disabled_list) > 1:
            header = f"{humanize_number(len(disabled_list))} commands are disabled globally.\n"
        else:
            header = "1 command is disabled globally.\n"
        paged = [box(x) for x in pagify(humanize_list(disabled_list), page_length=1000)]
        paged[0] = header + paged[0]
        await ctx.send_interactive(paged)

    @commands.guild_only()
    @list_disabled.command(name="guild")
    async def list_disabled_guild(self, ctx: commands.Context):
        """List disabled commands in this server.

        **Example:**
            - `;command listdisabled guild`

        """
        disabled_list = await self.bot._config.guild(ctx.guild).disabled_commands()
        if not disabled_list:
            return await ctx.send(f"There aren't any disabled commands in {ctx.guild}.")

        if len(disabled_list) > 1:
            header = f"{humanize_number(len(disabled_list))} commands are disabled in {ctx.guild}.\n"
        else:
            header = f"1 command is disabled in {ctx.guild}.\n"
        paged = [box(x) for x in pagify(humanize_list(disabled_list), page_length=1000)]
        paged[0] = header + paged[0]
        await ctx.send_interactive(paged)

    @command_manager.group(name="disable", invoke_without_command=True)
    async def command_disable(self, ctx: commands.Context, *, command: CommandConverter):
        """Disable a command.

        If you're the bot owner, this will disable commands globally by default.
        Otherwise, this will disable commands on the current server.

        **Examples:**
            - `;command disable userinfo` - Disables the `userinfo` command in the Mod cog.
            - `;command disable urban` - Disables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to disable.

        """
        # Select the scope based on the author's privileges
        if await ctx.bot.is_owner(ctx.author):
            await ctx.invoke(self.command_disable_global, command=command)
        else:
            await ctx.invoke(self.command_disable_guild, command=command)

    @checks.is_owner()
    @command_disable.command(name="global")
    async def command_disable_global(self, ctx: commands.Context, *, command: CommandConverter):
        """Disable a command globally.

        **Examples:**
            - `;command disable global userinfo` - Disables the `userinfo` command in the Mod cog.
            - `;command disable global urban` - Disables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to disable globally.

        """
        if self.command_manager in command.parents or self.command_manager == command:
            await ctx.send("The command to disable cannot be `command` or any of its subcommands.")
            return

        if isinstance(command, commands.commands._RuleDropper):
            await ctx.send("This command is designated as being always available and cannot be disabled.")
            return

        async with ctx.bot._config.disabled_commands() as disabled_commands:
            if command.qualified_name not in disabled_commands:
                disabled_commands.append(command.qualified_name)

        if not command.enabled:
            await ctx.send("That command is already disabled globally.")
            return
        command.enabled = False

        await ctx.tick()

    @commands.guild_only()
    @command_disable.command(name="server", aliases=["guild"])
    async def command_disable_guild(self, ctx: commands.Context, *, command: CommandConverter):
        """Disable a command in this server only.

        **Examples:**
            - `;command disable server userinfo` - Disables the `userinfo` command in the Mod cog.
            - `;command disable server urban` - Disables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to disable for the current server.

        """
        if self.command_manager in command.parents or self.command_manager == command:
            await ctx.send("The command to disable cannot be `command` or any of its subcommands.")
            return

        if isinstance(command, commands.commands._RuleDropper):
            await ctx.send("This command is designated as being always available and cannot be disabled.")
            return

        if command.requires.privilege_level > await PrivilegeLevel.from_ctx(ctx):
            await ctx.send("You are not allowed to disable that command.")
            return

        async with ctx.bot._config.guild(ctx.guild).disabled_commands() as disabled_commands:
            if command.qualified_name not in disabled_commands:
                disabled_commands.append(command.qualified_name)

        if done := command.disable_in(ctx.guild):
            await ctx.tick()
        else:
            await ctx.send("That command is already disabled in this server.")

    @command_manager.group(name="enable", invoke_without_command=True)
    async def command_enable(self, ctx: commands.Context, *, command: CommandConverter):
        """Enable a command.

        If you're the bot owner, this will try to enable a globally disabled command by default.
        Otherwise, this will try to enable a command disabled on the current server.

        **Examples:**
            - `;command enable userinfo` - Enables the `userinfo` command in the Mod cog.
            - `;command enable urban` - Enables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to enable.

        """
        if await ctx.bot.is_owner(ctx.author):
            await ctx.invoke(self.command_enable_global, command=command)
        else:
            await ctx.invoke(self.command_enable_guild, command=command)

    @commands.is_owner()
    @command_enable.command(name="global")
    async def command_enable_global(self, ctx: commands.Context, *, command: CommandConverter):
        """Enable a command globally.

        **Examples:**
            - `;command enable global userinfo` - Enables the `userinfo` command in the Mod cog.
            - `;command enable global urban` - Enables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to enable globally.

        """
        async with ctx.bot._config.disabled_commands() as disabled_commands:
            with contextlib.suppress(ValueError):
                disabled_commands.remove(command.qualified_name)

        if command.enabled:
            await ctx.send("That command is already enabled globally.")
            return

        command.enabled = True
        await ctx.tick()

    @commands.guild_only()
    @command_enable.command(name="server", aliases=["guild"])
    async def command_enable_guild(self, ctx: commands.Context, *, command: CommandConverter):
        """Enable a command in this server.

        **Examples:**
            - `;command enable server userinfo` - Enables the `userinfo` command in the Mod cog.
            - `;command enable server urban` - Enables the `urban` command in the General cog.

        **Arguments:**
            - `<command>` - The command to enable for the current server.

        """
        if command.requires.privilege_level > await PrivilegeLevel.from_ctx(ctx):
            await ctx.send("You are not allowed to enable that command.")
            return

        async with ctx.bot._config.guild(ctx.guild).disabled_commands() as disabled_commands:
            with contextlib.suppress(ValueError):
                disabled_commands.remove(command.qualified_name)

        if done := command.enable_in(ctx.guild):
            await ctx.tick()
        else:
            await ctx.send("That command is already enabled in this server.")

    @checks.is_owner()
    @command_manager.command(name="disabledmsg")
    async def command_disabledmsg(self, ctx: commands.Context, *, message: str = ""):
        """Set the bot's response to disabled commands.

        Leave blank to send nothing.

        To include the command name in the message, include the `{command}` placeholder.

        **Examples:**
            - `;command disabledmsg This command is disabled`
            - `;command disabledmsg {command} is disabled`
            - `;command disabledmsg` - Sends nothing when a disabled command is attempted.

        **Arguments:**
            - `[message]` - The message to send when a disabled command is attempted.

        """
        await ctx.bot._config.disabled_command_msg.set(message)
        await ctx.tick()

    @checks.is_owner()
    @_set.group()
    async def ownernotifications(self, ctx: commands.Context):
        """Commands for configuring owner notifications.

        Owner notifications include usage of `;contact` and available
        Melanie updates.

        """

    @ownernotifications.command()
    async def optin(self, ctx: commands.Context):
        """Opt-in on receiving owner notifications.

        This is the default state.

        Note: This will only resume sending owner notifications to your DMs.
            Additional owners and destinations will not be affected.

        **Example:**
            - `;ownernotifications optin`

        """
        async with ctx.bot._config.owner_opt_out_list() as opt_outs:
            if ctx.author.id in opt_outs:
                opt_outs.remove(ctx.author.id)

        await ctx.tick()

    @ownernotifications.command()
    async def optout(self, ctx: commands.Context):
        """Opt-out of receiving owner notifications.

        Note: This will only stop sending owner notifications to your DMs.
            Additional owners and destinations will still receive notifications.

        **Example:**
            - `;ownernotifications optout`

        """
        async with ctx.bot._config.owner_opt_out_list() as opt_outs:
            if ctx.author.id not in opt_outs:
                opt_outs.append(ctx.author.id)

        await ctx.tick()

    @ownernotifications.command()
    async def adddestination(self, ctx: commands.Context, *, channel: Union[discord.TextChannel, int]):
        """Adds a destination text channel to receive owner notifications.

        **Examples:**
            - `;ownernotifications adddestination #owner-notifications`
            - `;ownernotifications adddestination 168091848718417920` - Accepts channel IDs.

        **Arguments:**
            - `<channel>` - The channel to send owner notifications to.

        """
        try:
            channel_id = channel.id
        except AttributeError:
            channel_id = channel

        async with ctx.bot._config.extra_owner_destinations() as extras:
            if channel_id not in extras:
                extras.append(channel_id)

        await ctx.tick()

    @ownernotifications.command(aliases=["remdestination", "deletedestination", "deldestination"])
    async def removedestination(self, ctx: commands.Context, *, channel: Union[discord.TextChannel, int]):
        """Removes a destination text channel from receiving owner notifications.

        **Examples:**
            - `;ownernotifications removedestination #owner-notifications`
            - `;ownernotifications deletedestination 168091848718417920` - Accepts channel IDs.

        **Arguments:**
            - `<channel>` - The channel to stop sending owner notifications to.

        """
        try:
            channel_id = channel.id
        except AttributeError:
            channel_id = channel

        async with ctx.bot._config.extra_owner_destinations() as extras:
            if channel_id in extras:
                extras.remove(channel_id)

        await ctx.tick()

    @ownernotifications.command()
    async def listdestinations(self, ctx: commands.Context):
        """Lists the configured extra destinations for owner notifications.

        **Example:**
            - `;ownernotifications listdestinations`

        """
        channel_ids = await ctx.bot._config.extra_owner_destinations()

        if not channel_ids:
            await ctx.send("There are no extra channels being sent to.")
            return

        data = []

        for channel_id in channel_ids:
            if channel := ctx.bot.get_channel(channel_id):
                # This includes the channel name in case the user can't see the channel.
                data.append(f"{channel.mention} {channel} ({channel.id})")
            else:
                data.append(f"Unknown channel with id: {channel_id}")

        output = "\n".join(data)
        for page in pagify(output):
            await ctx.send(page)

    # RPC handlers
    async def rpc_load(self, request):
        cog_name = request.params[0]

        spec = await self.bot._cog_mgr.find_cog(cog_name)
        if spec is None:
            msg = "No such cog found."
            raise LookupError(msg)

        self._cleanup_and_refresh_modules(spec.name)

        await self.bot.load_extension(spec)

    async def rpc_unload(self, request):
        cog_name = request.params[0]

        self.bot.unload_extension(cog_name)

    async def rpc_reload(self, request):
        await self.rpc_unload(request)
        await self.rpc_load(request)

    @checks.is_owner()
    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def ignore(self, ctx: commands.Context):
        """Commands to add servers or channels to the ignore list.

        The ignore list will prevent the bot from responding to commands in the configured locations.

        Note: Owners and Admins override the ignore list.

        """

    @ignore.command(name="list")
    async def ignore_list(self, ctx: commands.Context):
        """List the currently ignored servers and channels.

        **Example:**
            - `;ignore list`

        """
        for page in pagify(await self.count_ignored(ctx)):
            await ctx.maybe_send_embed(page)

    @ignore.command(name="channel")
    async def ignore_channel(self, ctx: commands.Context, channel: Optional[Union[discord.TextChannel, discord.CategoryChannel]] = None):
        """Ignore commands in the channel or category.

        Defaults to the current channel.

        Note: Owners, Admins, and those with Manage Channel permissions override ignored channels.

        **Examples:**
            - `;ignore channel #general` - Ignores commands in the #general channel.
            - `;ignore channel` - Ignores commands in the current channel.
            - `;ignore channel "General Channels"` - Use quotes for categories with spaces.
            - `;ignore channel 356236713347252226` - Also accepts IDs.

        **Arguments:**
            - `<channel>` - The channel to ignore. Can be a category channel.

        """
        if not channel:
            channel = ctx.channel
        if not await self.bot._ignored_cache.get_ignored_channel(channel):
            await self.bot._ignored_cache.set_ignored_channel(channel, True)
            await ctx.send("Channel added to ignore list.")
        else:
            await ctx.send("Channel already in ignore list.")

    @ignore.command(name="server", aliases=["guild"])
    @checks.admin_or_permissions(manage_guild=True)
    async def ignore_guild(self, ctx: commands.Context):
        """Ignore commands in this server.

        Note: Owners, Admins, and those with Manage Server permissions override ignored servers.

        **Example:**
            - `;ignore server` - Ignores the current server

        """
        guild = ctx.guild
        if not await self.bot._ignored_cache.get_ignored_guild(guild):
            await self.bot._ignored_cache.set_ignored_guild(guild, True)
            await ctx.send("This server has been added to the ignore list.")
        else:
            await ctx.send("This server is already being ignored.")

    @checks.is_owner()
    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def unignore(self, ctx: commands.Context):
        """Commands to remove servers or channels from the ignore list."""

    @unignore.command(name="channel")
    async def unignore_channel(self, ctx: commands.Context, channel: Optional[Union[discord.TextChannel, discord.CategoryChannel]] = None):
        """Remove a channel or category from the ignore list.

        Defaults to the current channel.

        **Examples:**
            - `;unignore channel #general` - Unignores commands in the #general channel.
            - `;unignore channel` - Unignores commands in the current channel.
            - `;unignore channel "General Channels"` - Use quotes for categories with spaces.
            - `;unignore channel 356236713347252226` - Also accepts IDs. Use this method to unignore categories.

        **Arguments:**
            - `<channel>` - The channel to unignore. This can be a category channel.

        """
        if not channel:
            channel = ctx.channel

        if await self.bot._ignored_cache.get_ignored_channel(channel):
            await self.bot._ignored_cache.set_ignored_channel(channel, False)
            await ctx.send("Channel removed from ignore list.")
        else:
            await ctx.send("That channel is not in the ignore list.")

    @unignore.command(name="server", aliases=["guild"])
    @checks.admin_or_permissions(manage_guild=True)
    async def unignore_guild(self, ctx: commands.Context):
        """Remove this server from the ignore list.

        **Example:**
            - `;unignore server` - Stops ignoring the current server

        """
        guild = ctx.message.guild
        if await self.bot._ignored_cache.get_ignored_guild(guild):
            await self.bot._ignored_cache.set_ignored_guild(guild, False)
            await ctx.send("This server has been removed from the ignore list.")
        else:
            await ctx.send("This server is not in the ignore list.")

    async def count_ignored(self, ctx: commands.Context):
        category_channels: list[discord.CategoryChannel] = []
        text_channels: list[discord.TextChannel] = []
        if await self.bot._ignored_cache.get_ignored_guild(ctx.guild):
            return "This server is currently being ignored."
        for channel in ctx.guild.text_channels:
            if channel.category and channel.category not in category_channels and await self.bot._ignored_cache.get_ignored_channel(channel.category):
                category_channels.append(channel.category)
            if await self.bot._ignored_cache.get_ignored_channel(channel, check_category=False):
                text_channels.append(channel)

        cat_str = humanize_list([c.name for c in category_channels]) if category_channels else "None"
        chan_str = humanize_list([c.mention for c in text_channels]) if text_channels else "None"
        return f"Currently ignored categories: {cat_str}\nChannels: {chan_str}"
