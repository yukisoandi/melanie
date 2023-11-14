from .savepic import Savepic


async def setup(bot):
    bot.add_cog(Savepic(bot))
