from __future__ import annotations

from pathlib import Path

import ujson
from melaniebot.core.bot import Melanie

from .phenutils import PhenUtils

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = ujson.load(fp)["end_user_data_statement"]


async def setup(bot: Melanie) -> None:
    bot.add_cog(PhenUtils(bot))
