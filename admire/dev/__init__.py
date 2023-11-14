from .dev import Dev


async def setup(bot):
    bot.add_cog(Dev())
