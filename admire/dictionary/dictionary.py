from __future__ import annotations

import argparse
import contextlib

import discord
import regex as re
import ujson as json
from loguru import logger as log
from melaniebot.core import commands
from melaniebot.core.commands import BadArgument, Converter
from melaniebot.core.utils.chat_formatting import pagify

from dictionary.helpers import get_soup_object


class NoExitParser(argparse.ArgumentParser):
    def error(self, message: str):
        raise BadArgument


class Gargs(Converter):
    async def convert(self, ctx, argument) -> dict[str, int]:
        argument = argument.replace("—", "--")
        parser = NoExitParser(description="Grammar argument parser", add_help=False)

        parser.add_argument("--meaning-like", "--ml", nargs="*", dest="ml", default=[])
        parser.add_argument("--spelled-like", "--sp", nargs="?", dest="sp", default=[])
        parser.add_argument("--sounds-like", "--sl", nargs="?", dest="sl", default=[])
        parser.add_argument("--rhymes-with", "--rw", nargs="?", dest="rw", default=[])
        parser.add_argument("--adjectives-for", "--af", nargs="?", dest="af", default=[])
        parser.add_argument("--nouns-for", "--nf", nargs="?", dest="nf", default=[])
        parser.add_argument("--comes-before", "--cb", nargs="*", dest="ca", default=[])
        parser.add_argument("--comes-after", "--ca", nargs="*", dest="cb", default=[])
        parser.add_argument("--topics", "--t", nargs="*", dest="t", default=[])
        parser.add_argument("--synonyms-for", "--sf", nargs="*", dest="sf", default=[])
        parser.add_argument("--antonyms-for", "--anf", nargs="*", dest="anf", default=[])
        parser.add_argument("--kind-of", "--ko", nargs="?", dest="ko", default=[])
        parser.add_argument("--more-specific-than", "--mst", nargs="?", dest="mso", default=[])
        parser.add_argument("--homophones", "--h", nargs="?", dest="h", default=[])

        try:
            vals = vars(parser.parse_args(argument.split(" ")))
        except Exception as error:
            raise BadArgument from error

        data = {}
        if vals["ml"]:
            data["ml"] = " ".join(vals["ml"])
        if vals["sp"]:
            data["sp"] = vals["sp"]
        if vals["sl"]:
            data["sl"] = vals["sl"]
        if vals["rw"]:
            data["rel_rhy"] = vals["rw"]
        if vals["af"]:
            data["rel_jjb"] = vals["af"]
        if vals["nf"]:
            data["rel_jja"] = vals["nf"]
        if vals["ca"]:
            data["lc"] = " ".join(vals["ca"])
        if vals["cb"]:
            data["rc"] = " ".join(vals["cb"])
        if vals["t"]:
            if len(vals["t"]) > 5:
                msg = "Topic can only be five words"
                raise BadArgument(msg)
            data["topics"] = " ".join(vals["t"])
        if vals["sf"]:
            data["rel_syn"] = " ".join(vals["sf"])
        if vals["anf"]:
            data["rel_ant"] = " ".join(vals["anf"])
        if vals["ko"]:
            data["rel_spc"] = vals["ko"]
        if vals["mso"]:
            data["rel_gen"] = vals["mso"]
        if vals["h"]:
            data["rel_hom"] = vals["h"]

        data["max"] = 10

        return data


URL = "http://api.datamuse.com/words"


class Dictionary(commands.Cog):
    """Word, yo Parts of this cog are adapted from the PyDictionary library."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @property
    def session(self):
        return self.bot.aio

    @commands.command()
    async def grammar(self, ctx, *, args: Gargs):
        """Get words related to the passed arguments.

        Arguments must have `--` before them.
           `meaning-like`/`ml`: Get words that mean close to what the passed word means.
           `spelled-like`/`sp`: Get words that are spelled like the passed word.
           `sounds-like`/`sl`: Get words that sound like the passed word.
           `rhymes-with`/`rw`: Get words that rhyme with the passed word.
           `adjectives-for`/`af`: Get adjectives for the passed noun.
           `nouns-for`/`nf`: Get nouns for the passed adjective.
           `comes-before`/`cb`: Get words that usually come before the passed word.
           `comes-after`/`ca`: Get words that usually come after the passed word.
           `topics`: Get words that are related to the passed topic.  Max 5 words.
           `synonyms-for`/`sf`: Get synonyms for the passed word.
           `antonyms-for`/`anf`: Get antonyms for the passed word.
           `kind-of`/`ko`: Get the kind of what the passed word is (Computer -> Machine).
           `more-specific-than`/`mst`: Get more specific nouns for the passed word (Ex: Machine -> Computer).
           `homophones`/`h`: Get homophones of the passed word.

        """
        data = args

        async with self.bot.aio.get(URL, params=data) as r:
            if r.status != 200:
                return await ctx.send(f"Invalid status code: {r.status}")
            text = await r.json()
            sending = "Here are the top 10 words that came close to your filters:\n```\n"
            for x in text:
                sending += x["word"] + "\n"
            sending += "```"
            await ctx.send(sending)

    @commands.command()
    async def define(self, ctx, *, word: str) -> None:
        """Displays definitions of a given word."""
        search_msg = await ctx.send("Searching...")
        search_term = word.split(" ", 1)[0]
        result = await self._definition(ctx, search_term)
        str_buffer = ""
        if not result:
            with contextlib.suppress(discord.NotFound):
                await search_msg.delete()
            await ctx.send("This word is not in the dictionary.")
            return
        for key in result:
            str_buffer += f"\n**{key}**: \n"
            counter = 1
            j = False
            for val in result[key]:
                if val.startswith("("):
                    str_buffer += f"{counter}. *{val})* "
                    counter += 1
                    j = True
                elif j:
                    str_buffer += f"{val}\n"
                    j = False
                else:
                    str_buffer += f"{counter}. {val}\n"
                    counter += 1
        with contextlib.suppress(discord.NotFound):
            await search_msg.delete()
        for page in pagify(str_buffer, delims=["\n"]):
            await ctx.send(page)

    async def _definition(self, ctx, word):
        data = await get_soup_object(f"http://wordnetweb.princeton.edu/perl/webwn?s={word}")
        if not data:
            return await ctx.send("Error fetching data.")
        types = data.findAll("h3")
        len(types)
        lists = data.findAll("ul")
        out = {}
        if not lists:
            return
        for a in types:
            reg = str(lists[types.index(a)])
            meanings = []
            for x in re.findall(r">\s\((.*?)\)\s<", reg):
                if "often followed by" in x:
                    pass
                elif len(x) > 5 or " " in str(x):
                    meanings.append(x)
            name = a.text
            out[name] = meanings
        return out

    @commands.command()
    async def antonym(self, ctx, *, word: str) -> None:
        """Displays antonyms for a given word."""
        search_term = word.split(" ", 1)[0]
        result = await self._antonym_or_synonym(ctx, "antonyms", search_term)
        if not result:
            await ctx.send("This word is not in the dictionary or nothing was found.")
            return

        result_text = "*, *".join(result)
        msg = f"Antonyms for **{search_term}**: *{result_text}*"
        for page in pagify(msg, delims=["\n"]):
            await ctx.send(page)

    @commands.command()
    async def synonym(self, ctx, *, word: str) -> None:
        """Displays synonyms for a given word."""
        search_term = word.split(" ", 1)[0]
        result = await self._antonym_or_synonym(ctx, "synonyms", search_term)
        if not result:
            await ctx.send("This word is not in the dictionary or nothing was found.")
            return

        result_text = "*, *".join(result)
        msg = f"Synonyms for **{search_term}**: *{result_text}*"
        for page in pagify(msg, delims=["\n"]):
            await ctx.send(page)

    async def _antonym_or_synonym(self, ctx, lookup_type, word):
        if lookup_type not in ["antonyms", "synonyms"]:
            return None
        data = await get_soup_object(f"http://www.thesaurus.com/browse/{word}")
        if not data:
            await ctx.send("Error getting information from the website.")
            return

        website_data = None
        script = data.find_all("script")
        for item in script:
            if item.string and "window.INITIAL_STATE" in item.string:
                content = item.string
                content = content.lstrip("window.INITIAL_STATE =").rstrip(";")
                content = content.replace("undefined", '"None"').replace(": true", ': "True"').replace(": false", ': "False"')

                try:
                    website_data = json.loads(content)
                except json.decoder.JSONDecodeError:
                    return None
                except Exception as e:
                    log.exception(e)
                    await ctx.send("Something broke. Check your console for more information.")
                    return None

        final = []
        if website_data:
            if tuna_api_data := website_data["searchData"]["tunaApiData"]:
                final.extend(syn["term"] for syn in tuna_api_data["posTabs"][0][lookup_type])
            else:
                return None
        return final
