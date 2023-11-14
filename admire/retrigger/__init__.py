from .retrigger import ReTrigger


async def setup(bot) -> None:
    cog = ReTrigger(bot)
    bot.add_cog(cog)
