from __future__ import annotations

from datetime import timedelta
from typing import Union

import regex as re
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from melaniebot.core import commands
from regex.regex import Pattern

# the following regex is slightly modified from Melanie
#
TIME_RE_STRING: str = "((?P<weeks>\\d+?)\\s?(weeks?|w))|((?P<days>\\d+?)\\s?(days?|d))|((?P<hours>\\d+?)\\s?(hours?|hrs|hr?))|((?P<minutes>\\d+?)\\s?(minutes?|mins?|m(?!o)))|((?P<seconds>\\d+?)\\s?(seconds?|secs?|s))"  # prevent matching "months"
TIME_RE: Pattern[str] = re.compile(TIME_RE_STRING, re.I)
QUESTION_RE: Pattern[str] = re.compile(r"([^;]+)(?<=\?)\s?", re.I)
OPTIONS_RE: Pattern[str] = re.compile(r"([\S\s]+)(?=;)[\S\s]+", re.I)
SPLIT_RE: Pattern[str] = re.compile(r";")
TIME_SPLIT: Pattern[str] = re.compile(r"t(?:ime)?=")
MULTI_RE: Pattern[str] = re.compile(r"(multi-vote)", re.I)


class PollOptions(Converter):
    """This will parse my defined multi response pattern and provide usable
    formats to be used in multiple reponses.
    """

    async def convert(self, ctx: commands.Context, argument: str) -> dict[str, Union[list[str], str, bool, timedelta]]:
        result: dict[str, Union[list[str], str, bool, timedelta]] = {}
        if MULTI_RE.findall(argument):
            result["multiple_votes"] = True
            argument = MULTI_RE.sub("", argument)
        (result, argument) = self.strip_question(result, argument)
        (result, argument) = self.strip_time(result, argument)
        (result, argument) = self.strip_options(result, argument)
        result["author_id"] = ctx.author.id
        return result

    def strip_question(self, result: dict[str, Union[list[str], str, bool, timedelta]], argument: str):
        match = QUESTION_RE.match(argument)
        if not match:
            msg = "That doesn't look like a question."
            raise BadArgument(msg)
        result["question"] = match[0]
        no_question = QUESTION_RE.sub("", argument)
        return (result, no_question)

    def strip_options(self, result: dict[str, Union[list[str], str, bool, timedelta]], argument: str):
        possible_options = OPTIONS_RE.match(argument)
        if not possible_options:
            msg = "You have no options for this poll."
            raise BadArgument(msg)
        options = [s.strip() for s in SPLIT_RE.split(possible_options[0]) if s.strip()]
        if len(options) > 20:
            msg = "Use less options for the poll. Max options: 20."
            raise BadArgument(msg)
        result["options"] = options
        no_options = OPTIONS_RE.sub("", argument)
        return (result, no_options)

    def strip_time(
        self,
        result: dict[str, Union[list[str], str, bool, timedelta]],
        argument: str,
    ) -> tuple[Union[dict[str, timedelta], dict[str, Union[list[str], bool, timedelta, str]]], str]:
        maybe_time = time_split[-1] if (time_split := TIME_SPLIT.split(argument)) else argument

        time_data = {}
        for time in TIME_RE.finditer(maybe_time):
            argument = argument.replace(time[0], "")
            for k, v in time.groupdict().items():
                if v:
                    time_data[k] = int(v)
        if time_data:
            result["duration"] = timedelta(**time_data)
        return (result, argument)
