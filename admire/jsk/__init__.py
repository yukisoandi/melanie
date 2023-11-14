from __future__ import annotations

import jishaku
import jishaku.cog
from jishaku.cog import OPTIONAL_FEATURES, STANDARD_FEATURES
from melaniebot.core.bot import Melanie

jishaku.Flags.RETAIN = True
jishaku.Flags.NO_DM_TRACEBACK = True
jishaku.Flags.FORCE_PAGINATOR = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.HIDE = True
jishaku.Flags.ALWAYS_DM_TRACEBACK = False


class Jishaku(*STANDARD_FEATURES, *OPTIONAL_FEATURES):
    """Jishaku ported to Red."""


def setup(bot: Melanie) -> None:
    bot.add_cog(Jishaku(bot=bot))
