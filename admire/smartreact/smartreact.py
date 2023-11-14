import copy
from contextlib import suppress

import discord
import orjson
from aiomisc.backoff import asyncretry
from melaniebot.core import Config, checks, commands
from melaniebot.core.bot import Melanie
from melaniebot.core.utils.chat_formatting import pagify

from melanie import create_task, get_redis, make_e


@asyncretry(max_tries=3, pause=0.2)
async def add_reaction(msg, reaction):
    with suppress(discord.errors.Forbidden, discord.errors.InvalidArgument, discord.errors.NotFound):
        await msg.add_reaction(reaction)


class SmartReact(commands.Cog):
    """Create automatic reactions when trigger words are typed in chat."""

    default_guild_settings = {"reactions": {}}

    def __init__(self, bot: Melanie) -> None:
        self.bot = bot
        self.conf = Config.get_conf(self, identifier=964952632)
        self.conf.register_guild(**self.default_guild_settings)
        self.active_tasks = [create_task(self.init_cache())]

    async def init_cache(self, guild_id=None) -> None:
        await self.bot.wait_until_ready()
        redis = get_redis()
        await self.bot.waits_uptime_for(30)
        if not guild_id:
            all_g = await self.conf.all_guilds()
            for gid, data in all_g.items():
                await redis.hset("reactcache", str(gid), orjson.dumps(data))
            return all_g
        elif self.bot.user.name == "melanie":
            data = await self.conf.guild_from_id(guild_id).all()
            if data:
                await redis.hset("reactcache", str(guild_id), orjson.dumps(data))

    @checks.has_permissions(manage_roles=True)
    @commands.guild_only()
    @commands.command(name="addreact")
    async def addreact(self, ctx, word, emoji) -> None:
        """Add an auto reaction to a word."""
        guild = ctx.message.guild
        message = ctx.message
        if guild.id == 1055621141131173979:
            return

        emoji = self.fix_custom_emoji(emoji)
        await self.create_smart_reaction(guild, word, emoji, message)
        await self.init_cache(ctx.guild.id)

    @checks.has_permissions(administrator=True)
    @commands.guild_only()
    @commands.command(name="delreact")
    async def delreact(self, ctx, word, emoji=None) -> None:
        """Delete an auto reaction to a word."""
        guild = ctx.message.guild
        message = ctx.message
        if emoji:
            emoji = self.fix_custom_emoji(emoji)
        await self.remove_smart_reaction(guild, word, message, emoji)

        await self.init_cache(ctx.guild.id)

    def fix_custom_emoji(self, emoji):
        if emoji[:2] not in ["<:", "<a"]:
            return emoji
        return e if (e := self.bot.get_emoji(int(emoji.split(":")[2][:-1]))) else None

    @checks.has_permissions(administrator=True)
    @commands.guild_only()
    @commands.command(name="listreact")
    async def listreact(self, ctx) -> None:
        """List reactions for this server."""
        emojis = await self.conf.guild(ctx.guild).reactions()
        emojis_copy = copy.deepcopy(emojis)
        msg = f"Smart Reactions for {ctx.guild.name}:\n"
        for emoji, words in emojis_copy.items():
            e = self.fix_custom_emoji(emoji)
            if (not e) or (len(words) == 0):
                del emojis[emoji]
                continue
            for command in words:
                msg += f"{emoji}: {command}\n"
        await self.conf.guild(ctx.guild).reactions.set(emojis)
        if len(emojis) == 0:
            msg += "None."
        for page in pagify(msg, delims=["\n"]):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    async def create_smart_reaction(self, guild, word, emoji, message) -> None:
        try:
            # Use the reaction to see if it's valid
            await message.add_reaction(emoji)
            emoji = str(emoji)
            reactions = await self.conf.guild(guild).reactions()
            if emoji in reactions:
                if word.lower() in reactions[emoji]:
                    await message.channel.send(embed=make_e("This smart reaction already exists.", 3))
                    return
                reactions[emoji].append(word.lower())
            else:
                reactions[emoji] = [word.lower()]
            await self.conf.guild(guild).reactions.set(reactions)
            await message.channel.send(embed=make_e("Successfully added this reaction."))

        except (discord.errors.HTTPException, discord.errors.InvalidArgument):
            await message.channel.send(embed=make_e("That's not an emoji I recognize. (might be custom!)", 3))

    async def remove_smart_reaction(self, guild, word, message, emoji=None):
        try:
            # Use the reaction to see if it's valid
            ctx = await self.bot.get_context(message)
            reactions = await self.conf.guild(guild).reactions()
            if not emoji:
                removed_from = 0
                temp = copy.deepcopy(reactions)
                for emoji_key, word_list in reactions.items():
                    if word in word_list:
                        temp[emoji_key].remove(word)
                        removed_from += 1
                if not removed_from:
                    return await ctx.send(embed=make_e("No reactions for that word existed..", 2))
                await self.conf.guild(guild).reactions.set(temp)
                return await ctx.send(embed=make_e(f"Removed **{word}** from {removed_from} reactions"))
            else:
                emoji = str(emoji)
                if emoji in reactions:
                    if word.lower() in reactions[emoji]:
                        reactions[emoji].remove(word.lower())
                        await self.conf.guild(guild).reactions.set(reactions)
                        await message.channel.send("Removed this smart reaction.")
                    else:
                        await message.channel.send(embed=make_e("That emoji is not used as a reaction for that word.", 3))
                else:
                    await message.channel.send(embed=make_e("There are no smart reactions which use this emoji.", 3))

        except (discord.errors.HTTPException, discord.errors.InvalidArgument):
            await message.channel.send(embed=make_e("That's not an emoji I recognize. (might be custom!)", 3))

    @commands.Cog.listener()
    async def on_message_no_cmd(self, message) -> None:
        if not message.guild:
            return
        if message.author == self.bot.user:
            return
        if message.author.bot:
            return
        guild = message.guild
        reacts = await self.bot.redis.hget("reactcache", str(guild.id))
        if not reacts:
            return
        reacts = orjson.loads(reacts)
        if "reactions" not in reacts:
            return
        reacts = reacts["reactions"]
        words = message.content.lower().split()
        for emoji in reacts:
            if {w.lower() for w in reacts[emoji]}.intersection(words):
                emoji = self.fix_custom_emoji(emoji)
                if not emoji:
                    continue
                try:
                    await add_reaction(message, emoji)
                except discord.errors.HTTPException:
                    async with self.conf.guild(guild).reactions() as reactions:
                        if emoji in reactions:
                            del reactions[emoji]
                    await self.init_cache(guild.id)
