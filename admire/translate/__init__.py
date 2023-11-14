from .translate import Translate


async def setup(bot) -> None:
    cog = Translate(bot)
    bot.add_cog(cog)
