from __future__ import annotations

from pathlib import Path

import ujson

from .welcome import Welcome

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = ujson.load(fp)["end_user_data_statement"]


def setup(bot) -> None:
    n = Welcome(bot)
    bot.add_cog(n)
