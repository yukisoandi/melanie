from melaniebot.core.bot import Melanie

from .categoryhelp import CategoryHelp


async def setup(bot: Melanie) -> None:
    help = bot.remove_command("help")

    cog = CategoryHelp(bot)
    cog.help2 = help
    bot.add_cog(cog)
