from __future__ import annotations

# lor.py  compat.py  display.py  drawing.py  exceptions.py  font.py  image.py  resource.py  sequence.py  version.py


def setup(bot) -> None:
    from .notsobot import NotSoBot

    cog = NotSoBot(bot)

    bot.add_cog(cog)
