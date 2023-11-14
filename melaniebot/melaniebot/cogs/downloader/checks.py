from __future__ import annotations

from melaniebot.core import commands

__all__ = ["do_install_agreement"]


def T_(x):
    return x


def _(s):
    return s


REPO_INSTALL_MSG = "You're about to add a 3rd party repository. The creator of Melanie and its community have no responsibility for any potential damage that the content of 3rd party repositories might cause.\n\nBy typing '**I agree**' you declare that you have read and fully understand the above message. This message won't be shown again until the next reboot.\n\nYou have **30** seconds to reply to this message."
_ = T_


async def do_install_agreement(ctx: commands.Context) -> bool:
    return True
