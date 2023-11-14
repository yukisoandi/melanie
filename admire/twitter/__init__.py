from .tweets import Twitter


def setup(bot):
    cog = Twitter(bot)
    bot.add_cog(cog)
