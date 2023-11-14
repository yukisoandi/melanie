from .chatgpt import ChatGPT


async def setup(bot):
    bot.add_cog(ChatGPT(bot))
