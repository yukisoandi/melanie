from __future__ import annotations

import ast
import asyncio
import inspect
import io
import textwrap
import traceback
import types
from contextlib import redirect_stdout
from copy import copy

import aiohttp
import discord
import regex as re

from . import checks, commands
from .commands import NoParseOptional as Optional
from .utils.chat_formatting import pagify
from .utils.predicates import MessagePredicate

"""
Notice:

95% of the below code came from R.Danny which can be found here:

https://github.com/Rapptz/RoboDanny/blob/master/cogs/repl.py
"""


def _(x):
    return x


START_CODE_BLOCK_RE = re.compile(r"^((```py(thon)?)(?=\s)|(```))")


class Dev(commands.Cog):
    """Various development focused utilities."""

    def __init__(self) -> None:
        super().__init__()
        self._last_result = None
        self.sessions = {}
        self.env_extensions = {}

    @staticmethod
    def async_compile(source, filename, mode):
        return compile(source, filename, mode, flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, optimize=0)

    @staticmethod
    async def maybe_await(coro):
        for _ in range(2):
            if inspect.isawaitable(coro):
                coro = await coro
            else:
                return coro
        return coro

    @staticmethod
    def cleanup_code(content):
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return START_CODE_BLOCK_RE.sub("", content)[:-3]

        # remove `foo`
        return content.strip("` \n")

    @classmethod
    def get_syntax_error(cls, e):
        """Format a syntax error to send to the user.

        Returns a string representation of the error formatted as a
        codeblock.

        """
        if e.text is None:
            return cls.get_pages(f"{e.__class__.__name__}: {e}")
        return cls.get_pages(f"{e.text}\n{'^':>{e.offset}}\n{type(e).__name__}: {e}")

    @staticmethod
    def get_pages(msg: str):
        """Pagify the given message for output to the user."""
        return pagify(msg, delims=["\n", " "], priority=True, shorten_by=10)

    @staticmethod
    def sanitize_output(ctx: commands.Context, input_: str) -> str:
        """Hides the bot's token from a string."""
        token = ctx.bot.http.token
        return re.sub(re.escape(token), "[EXPUNGED]", input_, re.I)

    def get_environment(self, ctx: commands.Context) -> dict:
        env = {
            "bot": ctx.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
            "asyncio": asyncio,
            "aiohttp": aiohttp,
            "discord": discord,
            "commands": commands,
            "_": self._last_result,
            "__name__": "__main__",
        }
        for name, value in self.env_extensions.items():
            try:
                env[name] = value(ctx)
            except Exception as e:
                traceback.clear_frames(e.__traceback__)
                env[name] = e
        return env

    @commands.command()
    @checks.is_owner()
    async def debug(self, ctx, *, code):
        """Evaluate a statement of python code.

        The bot will always respond with the return value of the code.
        If the return value of the code is a coroutine, it will be awaited,
        and the result of that will be the bot's response.

        Note: Only one statement may be evaluated. Using certain restricted
        keywords, e.g. yield, will result in a syntax error. For multiple
        lines or asynchronous code, see ;repl or ;eval.

        Environment Variables:
            ctx      - command invocation context
            bot      - bot object
            channel  - the current channel object
            author   - command author's member object
            message  - the command's message object
            discord  - discord.py library
            commands - melaniebot.core.commands
            _        - The result of the last dev command.

        """
        env = self.get_environment(ctx)
        code = self.cleanup_code(code)

        try:
            compiled = self.async_compile(code, "<string>", "eval")
            result = await self.maybe_await(eval(compiled, env))
        except SyntaxError as e:
            await ctx.send_interactive(self.get_syntax_error(e), box_lang="py")
            return
        except Exception as e:
            await ctx.send_interactive(self.get_pages(f"{type(e).__name__}: {e!s}"), box_lang="py")
            return

        self._last_result = result
        result = self.sanitize_output(ctx, str(result))

        await ctx.tick()
        await ctx.send_interactive(self.get_pages(result), box_lang="py")

    @commands.command(name="eval")
    @checks.is_owner()
    async def _eval(self, ctx, *, body: str):
        """Execute asynchronous code.

        This command wraps code into the body of an async function and then
        calls and awaits it. The bot will respond with anything printed to
        stdout, as well as the return value of the function.

        The code can be within a codeblock, inline code or neither, as long
        as they are not mixed and they are formatted correctly.

        Environment Variables:
            ctx      - command invocation context
            bot      - bot object
            channel  - the current channel object
            author   - command author's member object
            message  - the command's message object
            discord  - discord.py library
            commands - melaniebot.core.commands
            _        - The result of the last dev command.

        """
        env = self.get_environment(ctx)
        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = "async def func():\n%s" % textwrap.indent(body, "  ")

        try:
            compiled = self.async_compile(to_compile, "<string>", "exec")
            exec(compiled, env)
        except SyntaxError as e:
            return await ctx.send_interactive(self.get_syntax_error(e), box_lang="py")

        func = env["func"]
        result = None
        try:
            with redirect_stdout(stdout):
                result = await func()
        except Exception:
            printed = f"{stdout.getvalue()}{traceback.format_exc()}"
        else:
            printed = stdout.getvalue()
            await ctx.tick()

        if result is not None:
            self._last_result = result
            msg = f"{printed}{result}"
        else:
            msg = printed
        msg = self.sanitize_output(ctx, msg)

        await ctx.send_interactive(self.get_pages(msg), box_lang="py")

    @commands.group(invoke_without_command=True)
    @checks.is_owner()
    async def repl(self, ctx):
        """Open an interactive REPL.

        The REPL will only recognise code as messages which start with a
        backtick. This includes codeblocks, and as such multiple lines
        can be evaluated.

        """
        if ctx.channel.id in self.sessions:
            if self.sessions[ctx.channel.id]:
                await ctx.send("Already running a REPL session in this channel. Exit it with `quit`.")
            else:
                await ctx.send(f"Already running a REPL session in this channel. Resume the REPL with `{ctx.prefix}repl resume`.")
            return

        env = self.get_environment(ctx)
        env["__builtins__"] = __builtins__
        env["_"] = None
        self.sessions[ctx.channel.id] = True
        await ctx.send(f"Enter code to execute or evaluate. `exit()` or `quit` to exit. `{ctx.prefix}repl pause` to pause.")

        while True:
            response = await ctx.bot.wait_for("message", check=MessagePredicate.regex(r"^`", ctx))

            if not self.sessions[ctx.channel.id]:
                continue

            cleaned = self.cleanup_code(response.content)

            if cleaned in ("quit", "exit", "exit()"):
                await ctx.send("Exiting.")
                del self.sessions[ctx.channel.id]
                return

            executor = None
            if cleaned.count("\n") == 0:
                # single statement, potentially 'eval'
                try:
                    code = self.async_compile(cleaned, "<repl session>", "eval")
                except SyntaxError:
                    pass
                else:
                    executor = eval

            if executor is None:
                try:
                    code = self.async_compile(cleaned, "<repl session>", "exec")
                except SyntaxError as e:
                    await ctx.send_interactive(self.get_syntax_error(e), box_lang="py")
                    continue

            env["message"] = response
            stdout = io.StringIO()

            msg = ""

            try:
                with redirect_stdout(stdout):
                    if executor is None:
                        result = types.FunctionType(code, env)()
                    else:
                        result = executor(code, env)
                    result = await self.maybe_await(result)
            except Exception:
                value = stdout.getvalue()
                msg = f"{value}{traceback.format_exc()}"
            else:
                value = stdout.getvalue()
                if result is not None:
                    msg = f"{value}{result}"
                    env["_"] = result
                elif value:
                    msg = f"{value}"

            msg = self.sanitize_output(ctx, msg)

            try:
                await ctx.send_interactive(self.get_pages(msg), box_lang="py")
            except discord.Forbidden:
                pass
            except discord.HTTPException as e:
                await ctx.send(f"Unexpected error: `{e}`")

    @repl.command(aliases=["resume"])
    async def pause(self, ctx, toggle: Optional[bool] = None):
        """Pauses/resumes the REPL running in the current channel."""
        if ctx.channel.id not in self.sessions:
            await ctx.send("There is no currently running REPL session in this channel.")
            return

        if toggle is None:
            toggle = not self.sessions[ctx.channel.id]
        self.sessions[ctx.channel.id] = toggle

        if toggle:
            await ctx.send("The REPL session in this channel has been resumed.")
        else:
            await ctx.send("The REPL session in this channel is now paused.")

    @commands.command()
    @checks.is_owner()
    async def mock(self, ctx, user: discord.Member, *, command):
        """Mock another user invoking a command.
        The prefix must not be entered.
        """
        msg = copy(ctx.message)
        msg.author = user
        msg.content = ctx.prefix + command
        ctx.bot.dispatch("message", msg)

    @commands.command(name="mockmsg")
    @checks.is_owner()
    async def mock_msg(self, ctx, user: discord.Member, *, content: str):
        """Dispatch a message event as if it were sent by a different user.

        Only reads the raw content of the message. Attachments, embeds
        etc. are ignored.

        """
        old_author = ctx.author
        old_content = ctx.message.content
        ctx.message.author = user
        ctx.message.content = content

        ctx.bot.dispatch("message", ctx.message)

        # If we change the author and content back too quickly,
        # the bot won't process the mocked message in time.
        await asyncio.sleep(2)
        ctx.message.author = old_author
        ctx.message.content = old_content

    @commands.command()
    @checks.is_owner()
    async def bypasscooldowns(self, ctx, toggle: Optional[bool] = None):
        """Give bot owners the ability to bypass cooldowns.

        Does not persist through restarts.

        """
        if toggle is None:
            toggle = not ctx.bot._bypass_cooldowns
        ctx.bot._bypass_cooldowns = toggle

        if toggle:
            await ctx.send("Bot owners will now bypass all commands with cooldowns.")
        else:
            await ctx.send("Bot owners will no longer bypass all commands with cooldowns.")
