from __future__ import annotations

from pathlib import Path

import ujson
from melaniebot.core.bot import Melanie

from .inviteblocklist import InviteBlocklist

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = ujson.load(fp)["end_user_data_statement"]


def setup(bot: Melanie) -> None:
    cog = InviteBlocklist(bot)
    bot.add_cog(cog)
