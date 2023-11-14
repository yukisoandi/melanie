from __future__ import annotations

from datetime import datetime, timedelta

import regex as re
from loguru import logger as log

from melanie import create_task

from .warnsystem import WarnSystem


def _(x):
    return x


async def _save_backup(config) -> None:
    from datetime import datetime

    import ujson as json
    from melaniebot.core.data_manager import cog_data_path

    date = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
    path = cog_data_path(raw_name="WarnSystem") / f"settings-backup-{date}.json"
    full_data = {"260": {"GUILDS": await config.all_guilds(), "MODLOGS": await config.custom("MODLOGS").all()}}
    data = json.dumps(full_data)
    with open(path.absolute(), "w") as file:
        file.write(data)
    log.info(f"Backup file saved at '{path.absolute()}', now starting conversion...")


async def _convert_to_v1(bot, config) -> None:
    def get_datetime(time: str) -> datetime:
        if isinstance(time, int):
            return datetime.fromtimestamp(time)
        try:
            time = datetime.strptime(time, "%a %d %B %Y %H:%M:%S")
        except ValueError:
            # seconds were added in an update, this might be a case made before that update
            time = datetime.strptime(time, "%a %d %B %Y %H:%M")
        return time

    def get_timedelta(text: str) -> timedelta:
        # that one is especially hard to convert
        # time is stored like this: "3 hours, 2 minutes and 30 seconds"
        # why did I even do this fuck me
        if isinstance(text, int):
            return timedelta(seconds=text)
        time = timedelta()
        results = re.findall(time_pattern, text)
        for match in results:
            amount = int(match[0])
            unit = match[1]
            if unit in units_name[0]:
                time += timedelta(days=amount * 366)
            elif unit in units_name[1]:
                time += timedelta(days=amount * 30.5)
            elif unit in units_name[2]:
                time += timedelta(weeks=amount)
            elif unit in units_name[3]:
                time += timedelta(days=amount)
            elif unit in units_name[4]:
                time += timedelta(hours=amount)
            elif unit in units_name[5]:
                time += timedelta(minutes=amount)
            else:
                time += timedelta(seconds=amount)
        return time

    for guild in bot.guilds:
        # update temporary warn to a dict instead of a list
        warns = await config.guild(guild).temporary_warns()
        if warns != {}:
            if warns:
                new_dict = {}
                for case in warns:
                    member = case["member"]
                    del case["member"]
                    new_dict[member] = case
                await config.guild(guild).temporary_warns.set(new_dict)
            else:
                # config does not update [] to {}
                # we fill a dict with random values to force config to set a dict
                # then we empty that dict
                await config.guild(guild).temporary_warns.set({None: None})
                await config.guild(guild).temporary_warns.set({})
        # change the way time is stored
        # instead of a long and heavy text, we use seconds since epoch
        modlogs = await config.custom("MODLOGS", guild.id).all()
        units_name = {
            0: ("year", "years"),
            1: ("month", "months"),
            2: ("week", "weeks"),
            3: ("day", "days"),
            4: ("hour", "hours"),
            5: ("minute", "minutes"),
            6: ("second", "seconds"),
        }  # yes this can be translated
        separator = " and "
        time_pattern = re.compile(
            rf"(?P<time>\d+)(?: )(?P<unit>{units_name[0][0]}|{units_name[0][1]}|{units_name[1][0]}|{units_name[1][1]}|{units_name[2][0]}|{units_name[2][1]}|{units_name[3][0]}|{units_name[3][1]}|{units_name[4][0]}|{units_name[4][1]}|{units_name[5][0]}|{units_name[5][1]}|{units_name[6][0]}|{units_name[6][1]})(?:(,)|({separator}))?",
        )
        for member, modlog in modlogs.items():
            if member == "x":
                continue
            for i, log in enumerate(modlog["x"]):
                time = get_datetime(log["time"])
                modlogs[member]["x"][i]["time"] = int(time.timestamp())
                duration = log["duration"]
                if duration is not None:
                    modlogs[member]["x"][i]["duration"] = int(get_timedelta(duration).total_seconds())
                    del modlogs[member]["x"][i]["until"]
        if modlogs:
            await config.custom("MODLOGS", guild.id).set(modlogs)


async def setup(bot) -> None:
    n = WarnSystem(bot)

    bot.add_cog(n)
    await n.cache.init_automod_enabled()
    n.task = create_task(n.api._loop_task())
    if n.cache.automod_enabled:
        n.api.enable_automod()
    log.success("Cog successfully loaded on the instance.")
