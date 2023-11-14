import asyncio

import tuuid
from melaniebot.core.bot import Melanie

from fun.fun import Fun, commands
from melanie import log, make_e

bot: Melanie = bot  # type: ignore
ctx: commands.Context = ctx  # type: ignore


sem = asyncio.BoundedSemaphore(2)


async def run2(t):
    with log.catch(exclude=asyncio.CancelledError):
        async with sem:
            await t


async def run():
    fun: Fun = bot.get_cog("Fun")
    tasks = await fun.get_uncached_emotes_tasks()
    tid = tuuid.tuuid()
    done = 0
    total = len(tasks)
    tracker = await ctx.send(embed=make_e(f"Loaded 0/{total}"))

    async with asyncio.TaskGroup() as tg:
        _tasks = [tg.create_task(run2(x)) for x in tasks]
        for t in asyncio.as_completed(_tasks):
            with log.catch(exclude=asyncio.CancelledError):
                await t

            done += 1
            if not await bot.redis.ratelimited(tid, 1, 3):
                await tracker.edit(embed=make_e(f"Loaded {done}/{total}"))
        await tracker.edit(embed=make_e(f"Loaded {done}/{total}"))


await run()  # type: ignore
