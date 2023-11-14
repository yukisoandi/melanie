from __future__ import annotations

import asyncio
import contextlib
import typing
from io import BytesIO
from typing import Optional

import discord
from boltons.iterutils import redundant
from melaniebot.core import commands
from xxhash import xxh3_64_hexdigest
from zipstream.aiozipstream import AioZipStream

from fun.helpers.text import extract_url_format
from melanie import BaseModel, footer_gif, get_dask, make_e, yesno
from notsobot.converter import ImageFinder
from runtimeopt import offloaded

from .constants import (
    FILE_SIZE,
    HTTP_EXCEPTION,
    INVOKE_ERROR,
    ROLE_HIERARCHY,
    SAME_SERVER_ONLY,
    TIME_OUT,
    CreateGuildSticker,
    EditGuildSticker,
    ImageToolarge,
    V9Route,
)
from .helpers import (
    create_guild_sticker,
    generate_sticker_from_url,
    modify_guild_sticker,
)

if typing.TYPE_CHECKING:
    from discord.sticker import Sticker
    from melaniebot.core.bot import Melanie


class GuildEmote(BaseModel):
    id: int
    emote_hash: Optional[str]
    url: str
    formatted_name: str
    name: str


class EmojiTools(commands.Cog):
    """Tools for Managing Custom Emojis."""

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot

    @staticmethod
    def _ext(e: typing.Union[discord.Emoji, discord.PartialEmoji]):
        return ".gif" if e.animated else ".png"

    @staticmethod
    async def _convert_emoji(ctx: commands.Context, emoji: str, partial_emoji: bool = True):
        try:
            if partial_emoji:
                return await commands.PartialEmojiConverter().convert(ctx=ctx, argument=emoji)
            return await commands.EmojiConverter().convert(ctx=ctx, argument=emoji)
        except commands.BadArgument as e:
            msg = f"Invalid emoji: {emoji}"
            raise commands.UserFeedbackCheckFailure(msg) from e

    async def sticker_from_ctx_or_msg(self, ctx: commands.Context, message: discord.Message) -> Sticker:
        channel: discord.TextChannel = ctx.channel
        if message:
            message = await channel.fetch_message(message.id)
            if not message.stickers:
                return await ctx.send(embed=make_e("No stickers in that message", status=3))

            sticker = message.stickers[0]

        elif not ctx.message.stickers:
            return await ctx.send(embed=make_e("No stickers in this message and no other message provided 🤨 ", status=3))

        else:
            sticker = ctx.message.stickers[0]

        return sticker

    @commands.guild_only()
    @commands.admin_or_permissions(manage_emojis=True)
    @commands.group(name="sticker", aliases=["stick", "stickers"])
    async def sticker(self, ctx: commands.Context) -> None:
        """Stckers."""

    @sticker.command(name="download")
    async def sticker_dl(self, ctx: commands.Context, message: Optional[discord.Message] = None, format: str = "png") -> None:
        """Download a sticker as an image.

        Provide the message ID that has the sticker.

        """
        async with asyncio.timeout(5), ctx.typing():
            sticker = await self.sticker_from_ctx_or_msg(ctx, message)
            embed = discord.Embed()
            embed.description = f"{sticker.name}"
            sticker_url = f"https://media.discordapp.net/stickers/{sticker.id}.png?size=4096"
            embed.set_image(url=sticker_url)
            embed.set_footer(text="melanie | sticker download", icon_url=footer_gif)
            await asyncio.sleep(0.2)

            await ctx.send(embed=embed)

    @sticker.command(name="steal")
    async def sticker_steal(self, ctx: commands.Context, message: Optional[discord.Message] = None, *, name: Optional[str] = None):
        """Steal stickers from another message or from your own message with the
        command.
        """
        if name and len(name) > 30:
            return await ctx.send(embed=make_e("Sticker names must be less than 30 characters", status=3))

        sticker = await self.sticker_from_ctx_or_msg(ctx, message)

        if not name:
            name = sticker.name

        async with ctx.typing():
            sticker_url = f"https://media.discordapp.net/stickers/{sticker.id}.png?size=320"

            r = await self.bot.curl.fetch(sticker_url)

            sticker_request = create_guild_sticker(
                ctx.cog.bot.http,
                ctx.guild.id,
                CreateGuildSticker(name=name, tags=f"melanie {name}", description=f"melanie sticker {name}"),
                discord.File(BytesIO(r.body), filename=f"{name}.png"),
                f"Guild sticker created by {ctx.author}",
            )
            try:
                await sticker_request
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(e, status=3))

            return await ctx.tick()

    @sticker.command(name="del", aliases=["remove", "delete", "rm"])
    async def sticker_del(self, ctx: commands.Context, message: Optional[discord.Message] = None):
        """Delete a sticker from the server."""
        async with ctx.typing():
            sticker = await self.sticker_from_ctx_or_msg(ctx, message)
            guild_id = ctx.guild.id

            route = V9Route("DELETE", "/guilds/{guild_id}/stickers/{sticker_id}", guild_id=guild_id, sticker_id=sticker.id)

            await ctx.cog.bot.http.request(route)

            return await ctx.tick()

    @sticker.command(name="name", aliases=["rename", "named"])
    async def sticker_name(self, ctx: commands.Context, message: Optional[discord.Message] = None, *, name: str):
        """Rename a sticker."""
        async with ctx.typing():
            if len(name) > 30:
                return await ctx.send(embed=make_e("Sticker names must be less than 30 characters", status=3))
            sticker = await self.sticker_from_ctx_or_msg(ctx, message)

            edit_sticker_request = modify_guild_sticker(
                http_client=ctx.cog.bot.http,
                guild_id=ctx.guild.id,
                sticker_id=sticker.id,
                payload=EditGuildSticker(name=name, tags=f"melanie {name}"),
                reason=f"Reanme requested by {ctx.author}",
            )

            try:
                await edit_sticker_request
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(e, status=3))

            return await ctx.tick()

    @sticker.command(name="emoji", aliases=["emote", "e", "icon"])
    async def sticker_emoji(self, ctx: commands.Context, emote, *, name: str = None):
        """Convert an emote to a sticker."""
        try:
            (url, format, extracted_name) = extract_url_format(emote)
        except IndexError:
            return await ctx.send("That doesn't look like an emoji to me!")
        if not name:
            name = extracted_name
        name = name[:29]
        img_url = url
        is_svg: bool = format == "svg"

        async with ctx.typing():
            try:
                async with asyncio.timeout(20):
                    sticker_b = await generate_sticker_from_url(img_url, is_svg)

            except TimeoutError:
                return await ctx.send(embed=make_e("Timed out while trying to generate that sticker", status=3))

            except ImageToolarge as e:
                return await ctx.send(embed=make_e(e, status=3))

            file = discord.File(BytesIO(sticker_b), filename=f"{name}.png")

            sticker_request = create_guild_sticker(
                ctx.cog.bot.http,
                ctx.guild.id,
                CreateGuildSticker(name=name, tags=f"melanie {name}", description=f"melanie sticker {name}"),
                file,
                f"Guild sticker created by {ctx.author}",
            )
            try:
                await sticker_request
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(e, status=3))

            return await ctx.tick()

    #  async def transparent(self, ctx: commands.Context, image: ImageFinder = None, alpha_matting: bool = False):
    @sticker.command(name="img", aliases=["image", "pic", "photo"])
    async def sticker_img(self, ctx: commands.Context, image: ImageFinder = None, *, name: str):
        """Add a sticker from an image.

        Handles the conversion to supported sticker formats.

        """
        if image is None:
            image = await ImageFinder().search_for_images(ctx)
        img_url = str(image[0])
        if len(name) > 30:
            return await ctx.send(embed=make_e("Sticker names must be less than 30 characters", status=3))

        async with ctx.typing():
            try:
                async with asyncio.timeout(10):
                    sticker_b = await generate_sticker_from_url(img_url)
            except TimeoutError:
                return await ctx.send(embed=make_e("Timed out while trying to generate that sticker", status=3))

            except ImageToolarge as e:
                return await ctx.send(embed=make_e(e, status=3))

            file = discord.File(BytesIO(sticker_b), filename=f"{name}.png")

            sticker_request = create_guild_sticker(
                ctx.cog.bot.http,
                ctx.guild.id,
                CreateGuildSticker(name=name, tags=f"melanie {name}", description=f"melanie sticker {name}"),
                file,
                f"Guild sticker created by {ctx.author}",
            )
            try:
                await sticker_request
            except discord.HTTPException as e:
                return await ctx.send(embed=make_e(e, status=3))

            return await ctx.tick()

    @commands.guild_only()
    @commands.admin_or_permissions(manage_emojis=True)
    @commands.group(name="emoji", aliases=["emojitools"])
    async def _emojitools(self, ctx: commands.Context) -> None:
        """Various tools for managing custom emojis in servers."""

    @_emojitools.command(name="info")
    async def _info(self, ctx: commands.Context, emoji: discord.Emoji):
        """Get info about a custom emoji from this server."""
        embed = discord.Embed(description=f"Emoji Information for {emoji}", color=await ctx.embed_color())
        embed.add_field(name="Name", value=f"{emoji.name}")
        embed.add_field(name="Emoji ID", value=f"{emoji.id}")
        embed.add_field(name="Animated", value=f"{emoji.animated}")
        embed.add_field(name="URL", value=f"[Image Link]({emoji.url})")
        embed.add_field(name="Creation (UTC)", value=f"{str(emoji.created_at)[:19]}")
        if ctx.guild.me.guild_permissions.manage_emojis:
            with contextlib.suppress(discord.HTTPException):
                e: discord.Emoji = await ctx.guild.fetch_emoji(emoji.id)
                embed.add_field(name="Author", value=f"{e.user.mention if e.user else 'Unknown'}")
        embed.add_field(name="Roles Allowed", value=f"{emoji.roles or 'Everyone'}")
        return await ctx.send(embed=embed)

    @_emojitools.group(name="delete", aliases=["remove", "del"])
    async def _delete(self, ctx: commands.Context) -> None:
        """Delete Server Custom Emojis."""

    @_delete.command(name="emojis", aliases=["emoji"], require_var_positional=True)
    async def _delete_emojis(self, ctx: commands.Context, *emoji_names: typing.Union[discord.Emoji, str]):
        """Delete custom emojis from the server."""
        async with ctx.typing():
            for e in emoji_names:
                if isinstance(e, str):
                    e: discord.Emoji = await self._convert_emoji(ctx, e, partial_emoji=False)
                elif e.guild_id != ctx.guild.id:
                    return await ctx.send(f"The following emoji is not in this server: {e}")
                await e.delete(reason=f"EmojiTools: deleted by {ctx.author}")
        return await ctx.send(f"The following emojis have been removed from this server: `{'`, `'.join([str(e) for e in emoji_names])}`")

    @staticmethod
    @offloaded
    def find_duplicates(emotes: list[GuildEmote]) -> list[GuildEmote]:
        """Find the duplicate emojis from the given list of GuildEmote."""
        return redundant(emotes, key=lambda x: x.emote_hash, groups=True)

    @staticmethod
    def calculate_emote_hash(emote: GuildEmote) -> GuildEmote:
        from melanie import worker_download

        data = worker_download(emote.url)
        emote.emote_hash = xxh3_64_hexdigest(data)
        return emote

    @_delete.command(name="duplicates")
    async def _delete_duplicates(self, ctx: commands.Context):
        """Find all duplicate emojis in the server.

        Melanie will calculate a hash for the emotes based off image
        contents so emotes with the same name will be deleted.

        """
        async with ctx.typing():
            msg: discord.Message = await ctx.send(
                embed=make_e("Calculating hashes for all emotes and finding duplicates...", status="info", tip="this may take a while"),
            )
            dask = get_dask()

            prepared_emotes = [GuildEmote(id=em.id, url=str(em.url), name=em.name, formatted_name=str(em)) for em in ctx.guild.emojis]

            try:
                async with asyncio.timeout(45):
                    calculate_hash_job = dask.map(self.calculate_emote_hash, prepared_emotes, batch_size=10)

                    hashed_emotes: list[GuildEmote] = await dask.gather(calculate_hash_job)

                    duplicates: list[GuildEmote] = await self.find_duplicates(hashed_emotes)

            except TimeoutError:
                await msg.edit(embed=make_e("Timeout finding duplicates", status=3))
                raise

            except asyncio.CancelledError:
                raise
            except Exception:
                await msg.edit(embed=make_e("Issue finding duplicates. This error has been reported", status=3))
                raise
            await msg.delete(delay=5)
            if not duplicates:
                return await ctx.send(embed=make_e("No duplicate emotes found!"))

            def get_emotes_with_hash(hash_id) -> list[GuildEmote]:
                return [e for e in hashed_emotes if e.emote_hash == hash_id]

            embed = discord.Embed()
            embed.set_footer(text="melanie ^_^", icon_url=footer_gif)
            embed.title = "Duplicates Found!"
            for i, group in enumerate(duplicates, start=1):
                emote = group[0]
                dups = get_emotes_with_hash(emote.emote_hash)
                embed.add_field(name=f"#{i} {emote.emote_hash}", value="".join(f"{em.formatted_name}: `{em.name}` \n" for em in dups))

            await ctx.send(embed=embed)
            confirmed, _msg = await yesno(f"I found {len(duplicates)} duplicate(s). Should I delete them?")
            if confirmed:
                for group in duplicates:
                    for em in group[:-1]:
                        emote = self.bot.get_emoji(em.id)
                        if not emote:
                            continue
                        async with asyncio.timeout(30):
                            await emote.delete()

            return await ctx.tick()

    @commands.cooldown(rate=1, per=60)
    @_delete.command(name="all")
    async def _delete_all(self, ctx: commands.Context, enter_true_to_confirm: bool):
        """Delete all specific custom emojis from the server."""
        if not enter_true_to_confirm:
            return await ctx.send("Please provide `true` as the parameter to confirm.")

        async with ctx.typing():
            counter = 0
            for e in ctx.guild.emojis:
                await e.delete()
                counter += 1

        return await ctx.send(f"All {counter} custom emojis have been removed from this server.")

    @_emojitools.group(name="add")
    async def _add(self, ctx: commands.Context) -> None:
        """Add Custom Emojis to Server."""

    @commands.cooldown(rate=1, per=15)
    @_add.command(name="emoji")
    async def _add_emoji(self, ctx: commands.Context, emoji: discord.PartialEmoji, name: str = None):
        """Add an emoji to this server (leave `name` blank to use the emoji's
        original name).
        """
        async with ctx.typing():
            try:
                final_emoji = await asyncio.wait_for(
                    ctx.guild.create_custom_emoji(
                        name=name or emoji.name,
                        image=await emoji.url.read(),
                        reason=f"EmojiTools: emoji added by {ctx.author.name}#{ctx.author.discriminator}",
                    ),
                    timeout=3600,
                )
            except TimeoutError:
                return await ctx.send(TIME_OUT)
            except commands.CommandInvokeError:
                return await ctx.send(INVOKE_ERROR)
            except discord.HTTPException:
                return await ctx.send(HTTP_EXCEPTION)

        return await ctx.send(f"{final_emoji} has been added to this server!")

    @commands.cooldown(rate=1, per=30)
    @_add.command(name="emojis", require_var_positional=True)
    async def _add_emojis(self, ctx: commands.Context, *emojis: str):
        """Add some emojis to this server."""
        async with ctx.typing():
            added_emojis = []
            for e in emojis:
                em = await self._convert_emoji(ctx, e)
                try:
                    fe = await asyncio.wait_for(
                        ctx.guild.create_custom_emoji(
                            name=em.name,
                            image=await em.url.read(),
                            reason=f"EmojiTools: emoji added by {ctx.author.name}#{ctx.author.discriminator}",
                        ),
                        timeout=10,
                    )
                    added_emojis.append(fe)
                except TimeoutError:
                    return await ctx.send(TIME_OUT)
                except commands.CommandInvokeError:
                    return await ctx.send(INVOKE_ERROR)
                except discord.HTTPException:
                    return await ctx.send(HTTP_EXCEPTION)

        return await ctx.send(f"{len(added_emojis)} emojis were added to this server: {' '.join([str(e) for e in added_emojis])}")

    @commands.cooldown(rate=1, per=15)
    @_add.command(name="fromreaction")
    async def _add_from_reaction(self, ctx: commands.Context, specific_reaction: str, message: discord.Message, new_name: str = None):
        """Add an emoji to this server from a specific reaction on a message."""
        final_emoji = None
        async with ctx.typing():
            for r in message.reactions:
                if r.custom_emoji and r.emoji.name == specific_reaction:
                    try:
                        final_emoji = await asyncio.wait_for(
                            ctx.guild.create_custom_emoji(
                                name=new_name or r.emoji.name,
                                image=await r.emoji.url.read(),
                                reason=f"EmojiTools: emoji added by {ctx.author.name}#{ctx.author.discriminator}",
                            ),
                            timeout=10,
                        )
                    except TimeoutError:
                        return await ctx.send(TIME_OUT)
                    except commands.CommandInvokeError:
                        return await ctx.send(INVOKE_ERROR)
                    except discord.HTTPException:
                        return await ctx.send(HTTP_EXCEPTION)

        if final_emoji:
            return await ctx.send(f"{final_emoji} has been added to this server!")
        else:
            return await ctx.send(f"No reaction called `{specific_reaction}` was found on that message!")

    @commands.cooldown(rate=1, per=30)
    @_add.command(name="allreactionsfrom")
    async def _add_all_reactions_from(self, ctx: commands.Context, message: discord.Message):
        """Add emojis to this server from all reactions in a message."""
        async with ctx.typing():
            added_emojis = []
            for r in message.reactions:
                if not r.custom_emoji:
                    continue
                try:
                    fe = await asyncio.wait_for(
                        ctx.guild.create_custom_emoji(
                            name=r.emoji.name,
                            image=await r.emoji.url.read(),
                            reason=f"EmojiTools: emoji added by {ctx.author.name}#{ctx.author.discriminator}",
                        ),
                        timeout=10,
                    )
                    added_emojis.append(fe)
                except TimeoutError:
                    return await ctx.send(TIME_OUT)
                except commands.CommandInvokeError:
                    return await ctx.send(INVOKE_ERROR)
                except discord.HTTPException:
                    return await ctx.send(HTTP_EXCEPTION)

        return await ctx.send(f"{len(added_emojis)} emojis were added to this server: {' '.join([str(e) for e in added_emojis])}")

    @commands.cooldown(rate=1, per=15)
    @commands.admin_or_permissions(manage_emojis=True)
    @_add.command(name="fromimage")
    async def _add_from_image(self, ctx: commands.Context, name: str = None):
        """Add an emoji to this server from a provided image.

        The attached image should be in one of the following formats:
        `.png`, `.jpg`, or `.gif`.

        """
        async with ctx.typing():
            if len(ctx.message.attachments) > 1:
                return await ctx.send("Please only attach 1 file!")

            if len(ctx.message.attachments) < 1:
                return await ctx.send("Please attach an image!")

            if not ctx.message.attachments[0].filename.endswith((".png", ".jpg", ".gif")):
                return await ctx.send("Please make sure the uploaded image is a `.png`, `.jpg`, or `.gif` file!")

            image = await ctx.message.attachments[0].read()

            try:
                new = await asyncio.wait_for(
                    ctx.guild.create_custom_emoji(
                        name=name or ctx.message.attachments[0].filename[:-4],
                        image=image,
                        reason=f"EmojiTools: emoji added by {ctx.author.name}#{ctx.author.discriminator}",
                    ),
                    timeout=10,
                )
            except TimeoutError:
                return await ctx.send(TIME_OUT)
            except commands.CommandInvokeError:
                return await ctx.send(INVOKE_ERROR)
            except discord.HTTPException:
                return await ctx.send("Something went wrong while adding emojis. Is the file size less than 256kb?")

        return await ctx.send(f"{new} has been added to this server!")

    # @commands.cooldown(rate=1, per=60)
    # @commands.admin_or_permissions(administrator=True)
    # @_add.command(name="fromzip")
    # async def _add_from_zip(self, ctx: commands.Context):
    #     """
    #     Add some emojis to this server from a provided .zip archive.

    #     The `.zip` archive should extract to a folder, which contains files in the formats `.png`, `.jpg`, or `.gif`.
    #     You can also use the `;emojitools tozip` command to get a zip archive, extract it, remove unnecessary emojis, then re-zip and upload.
    #     """

    #     async with ctx.typing():
    #         if len(ctx.message.attachments) > 1:

    #         if len(ctx.message.attachments) < 1:

    #         if not ctx.message.attachments[0].filename.endswith(".zip"):

    #         with ZipFile(BytesIO(await ctx.message.attachments[0].read())) as zip_file:

    #             for file_info in zip_file.infolist():

    #                 if not file_info.filename.endswith((".png", ".jpg", ".gif")):

    #                         ctx.guild.create_custom_emoji(
    #                         ),

    @_emojitools.group(name="edit")
    async def _edit(self, ctx: commands.Context) -> None:
        """Edit Custom Emojis in the Server."""

    @commands.cooldown(rate=1, per=15)
    @_edit.command(name="name")
    async def _edit_name(self, ctx: commands.Context, emoji: discord.Emoji, name: str):
        """Edit the name of a custom emoji from this server."""
        if emoji.guild_id != ctx.guild.id:
            return await ctx.send(SAME_SERVER_ONLY)
        await emoji.edit(name=name, reason=f"EmojiTools: edit requested by {ctx.author}")
        return await ctx.tick()

    @commands.cooldown(rate=1, per=15)
    @_edit.command(name="roles")
    async def _edit_roles(self, ctx: commands.Context, emoji: discord.Emoji, *roles: discord.Role):
        """Edit the roles to which the usage of a custom emoji from this server is
        restricted.
        """
        if emoji.guild_id != ctx.guild.id:
            return await ctx.send(SAME_SERVER_ONLY)
        for r in roles:
            if (r >= ctx.author.top_role and ctx.author != ctx.guild.owner) or r >= ctx.guild.me.top_role:
                return await ctx.send(ROLE_HIERARCHY)
        await emoji.edit(roles=roles, reason=f"EmojiTools: edit requested by {ctx.author}")
        return await ctx.tick()

    @_emojitools.group(name="tozip")
    async def _to_zip(self, ctx: commands.Context) -> None:
        """Get a `.zip` Archive of Emojis."""

    @staticmethod
    async def _generate_emoji(e):
        yield await e.url.read()

    async def _zip_emojis(self, emojis: list, file_name: str):
        emojis_list: list = [{"stream": self._generate_emoji(e), "name": f"{e.name}{self._ext(e)}"} for e in emojis]
        stream = AioZipStream(emojis_list, chunksize=32768)
        with BytesIO() as z:
            async for chunk in stream.stream():
                z.write(chunk)
            z.seek(0)
            zip_file: discord.File = discord.File(z, filename=file_name)

        return zip_file

    @commands.cooldown(rate=1, per=30)
    @_to_zip.command(name="emojis", require_var_positional=True)
    async def _to_zip_emojis(self, ctx: commands.Context, *emojis: str):
        """Get a `.zip` archive of the provided emojis.

        The returned `.zip` archive can be used for the `;emojitools
        add fromzip` command.

        """
        async with ctx.typing():
            actual_emojis: list = [await self._convert_emoji(ctx, e) for e in emojis]
            file: discord.File = await self._zip_emojis(actual_emojis, "emojis.zip")

        try:
            return await ctx.send(f"{len(emojis)} emojis were saved to this `.zip` archive!", file=file)
        except discord.HTTPException:
            return await ctx.send(FILE_SIZE)

    @commands.cooldown(rate=1, per=60)
    @_to_zip.command(name="server")
    async def _to_zip_server(self, ctx: commands.Context):
        """Get a `.zip` archive of all custom emojis in the server.

        The returned `.zip` archive can be used for the `;emojitools
        add fromzip` command.

        """
        async with ctx.typing():
            file: discord.File = await self._zip_emojis(ctx.guild.emojis, f"{ctx.guild.name}.zip")

        try:
            return await ctx.send(f"{len(ctx.guild.emojis)} emojis were saved to this `.zip` archive!", file=file)
        except discord.HTTPException:
            return await ctx.send(FILE_SIZE)
