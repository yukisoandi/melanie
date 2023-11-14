from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
from copy import deepcopy
from pathlib import Path

import aiohttp
import httpx
import orjson
from loguru import logger as log
from melanie import MelanieRedis, orjson_dumps2
from runtimeopt import loop_factory
from runtimeopt.preload_logger import build_logger
from tornado.curl_httpclient import CurlAsyncHTTPClient
from tornado.httpclient import AsyncHTTPClient

from melaniebot.core import data_manager, drivers
from melaniebot.core.bot import Melanie
from melaniebot.core.cli import confirm, interactive_config, parse_cli_flags
from melaniebot.setup import get_data_dir, get_name, save_config

Gb = 1073741824
INSTANCE_NAME = os.environ


def _get_instance_names():
    data = orjson.loads(data_manager.config_file.read_bytes())
    return sorted(data.keys())


def list_instances():
    if not data_manager.config_file.exists():
        print("No instances have been configured! Configure one using `melaniebot-setup` before trying to run the bot!")

        sys.exit(1)
    else:
        text = "Configured Instances:\n\n"
        for instance_name in _get_instance_names():
            text += f"{instance_name}\n"
        print(text)
        sys.exit(0)


async def edit_instance(melanie, cli_flags):
    no_prompt = cli_flags.no_prompt
    token = cli_flags.token
    owner = cli_flags.owner
    prefix = cli_flags.prefix
    old_name = cli_flags.instance_name
    new_name = cli_flags.edit_instance_name
    data_path = cli_flags.edit_data_path
    copy_data = cli_flags.copy_data
    confirm_overwrite = cli_flags.overwrite_existing_instance

    if data_path is None and copy_data:
        print("--copy-data can't be used without --edit-data-path argument")
        sys.exit(1)
    if new_name is None and confirm_overwrite:
        print("--overwrite-existing-instance can't be used without --edit-instance-name argument")
        sys.exit(1)
    if no_prompt and all(to_change is None for to_change in (token, owner, new_name, data_path)) and not prefix:
        print(
            "No arguments to edit were provided. Available arguments (check help for more information): --edit-instance-name, --edit-data-path, --copy-data, --owner, --token, --prefix",
        )
        sys.exit(1)

    await _edit_token(melanie, token, no_prompt)
    await _edit_prefix(melanie, prefix, no_prompt)
    await _edit_owner(melanie, owner, no_prompt)

    data = deepcopy(data_manager.basic_config)
    name = _edit_instance_name(old_name, new_name, confirm_overwrite, no_prompt)
    _edit_data_path(data, name, data_path, copy_data, no_prompt)

    save_config(name, data)
    if old_name != name:
        save_config(old_name, {}, remove=True)


async def _edit_token(melanie, token, no_prompt):
    if token:
        if len(token) < 50:
            print("The provided token doesn't look a valid Discord bot token. Instance's token will remain unchanged.\n")
            return
        await melanie._config.token.set(token)
    elif not no_prompt and confirm("Would you like to change instance's token?", default=False):
        await interactive_config(melanie, False, True, print_header=False)
        print("Token updated.\n")


async def _edit_prefix(melanie, prefix, no_prompt):
    if prefix:
        prefixes = sorted(prefix, reverse=True)
        await melanie._config.prefix.set(prefixes)
    elif not no_prompt and confirm("Would you like to change instance's prefixes?", default=False):
        print("Enter the prefixes, separated by a space (please note that prefixes containing a space will need to be added with ;set prefix)")
        while True:
            prefixes = input("> ").strip().split()
            if not prefixes:
                print("You need to pass at least one prefix!")
                continue
            prefixes = sorted(prefixes, reverse=True)
            await melanie._config.prefix.set(prefixes)
            print("Prefixes updated.\n")
            break


async def _edit_owner(melanie, owner, no_prompt):
    if owner:
        if not (15 <= len(str(owner)) <= 20):
            print("The provided owner id doesn't look like a valid Discord user id. Instance's owner will remain unchanged.")
            return
        await melanie._config.owner.set(owner)
    elif not no_prompt and confirm("Would you like to change instance's owner?", default=False):
        print(
            "Remember:\nONLY the person who is hosting Melanie should be owner. This has SERIOUS security implications. The owner can access any data that is present on the host system.\n",
        )
        if confirm("Are you sure you want to change instance's owner?", default=False):
            print("Please enter a Discord user id for new owner:")
            while True:
                owner_id = input("> ").strip()
                if not (15 <= len(owner_id) <= 20 and owner_id.isdecimal()):
                    print("That doesn't look like a valid Discord user id.")
                    continue
                owner_id = int(owner_id)
                await melanie._config.owner.set(owner_id)
                print("Owner updated.")
                break
        else:
            print("Instance's owner will remain unchanged.")
        print()


def _edit_instance_name(old_name, new_name, confirm_overwrite, no_prompt):
    if new_name:
        name = new_name
        if name in _get_instance_names() and not confirm_overwrite:
            name = old_name
            print(
                "An instance with this name already exists.\nIf you want to remove the existing instance and replace it with this one, run this command with --overwrite-existing-instance flag.",
            )
    elif not no_prompt and confirm("Would you like to change the instance name?", default=False):
        name = get_name()
        if name in _get_instance_names():
            print("WARNING: An instance already exists with this name. Continuing will overwrite the existing instance config.")
            if not confirm("Are you absolutely certain you want to continue with this instance name?", default=False):
                print("Instance name will remain unchanged.")
                name = old_name
            else:
                print("Instance name updated.")
        else:
            print("Instance name updated.")
        print()
    else:
        name = old_name
    return name


def _edit_data_path(data, instance_name, data_path, copy_data, no_prompt):
    if data_path:
        new_path = Path(data_path)
        try:
            exists = new_path.exists()
        except OSError:
            print("We were unable to check your chosen directory. Provided path may contain an invalid character. Data location will remain unchanged.")

        if not exists:
            try:
                new_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                print("We were unable to create your chosen directory. Data location will remain unchanged.")
        data["DATA_PATH"] = data_path
        if copy_data and not _copy_data(data):
            print("Can't copy data to non-empty location. Data location will remain unchanged.")
            data["DATA_PATH"] = data_manager.basic_config["DATA_PATH"]
    elif not no_prompt and confirm("Would you like to change the data location?", default=False):
        data["DATA_PATH"] = get_data_dir(instance_name)
        if confirm("Do you want to copy the data from old location?", default=True):
            if not _copy_data(data):
                print("Can't copy the data to non-empty location.")
                if not confirm("Do you still want to use the new data location?"):
                    data["DATA_PATH"] = data_manager.basic_config["DATA_PATH"]
                    print("Data location will remain unchanged.")
                    return
            print("Old data has been copied over to the new location.")
        print("Data location updated.")


def _copy_data(data):
    if Path(data["DATA_PATH"]).exists():
        if any(os.scandir(data["DATA_PATH"])):
            return False
        else:
            os.rmdir(data["DATA_PATH"])
    shutil.copytree(data_manager.basic_config["DATA_PATH"], data["DATA_PATH"])
    return True


async def run_bot(melanie: Melanie) -> None:
    driver_cls = drivers.get_driver_class()
    log.success("driver intializaed")
    log.info(data_manager.storage_details())
    await driver_cls.initialize(**data_manager.storage_details())
    log.warning("data: {}", data_manager._base_data_path())
    log.warning("engine: {}", data_manager.storage_type())
    cli_flags = melanie._cli_flags
    token = cli_flags.token or os.environ.get("RED_TOKEN", None) or await melanie._config.token()
    prefix = cli_flags.prefix or await melanie._config.prefix()
    if not token or not prefix:
        if cli_flags.no_prompt is not False:
            msg = "Token and prefix must be set in order to login."
            raise RuntimeError(msg)
        new_token = await interactive_config(melanie, token_set=bool(token), prefix_set=bool(prefix))
        if new_token:
            token = new_token
    log.warning("Starting main bot task....")
    await melanie.start(token, bot=True, cli_flags=melanie._cli_flags)


async def bot_main():
    redis = await MelanieRedis.from_url()
    cli_flags = parse_cli_flags(sys.argv[1:])

    name = cli_flags.instance_name
    if not name:
        return log.error("I need to know which instance name to load and none was provided.")

    curl = AsyncHTTPClient()
    assert isinstance(curl, CurlAsyncHTTPClient)
    data_manager.load_basic_configuration(cli_flags.instance_name)
    htx = httpx.AsyncClient(follow_redirects=True, cookies=httpx.Cookies(), timeout=httpx.Timeout(70))
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(),
        json_serialize=orjson_dumps2,
        connector=connector,
        timeout=aiohttp.ClientTimeout(60),
    ) as aio:
        log.warning("Loop: {}", asyncio.get_running_loop())
        mel = Melanie(
            description="melaniebot",
            dm_help=None,
            local_threadpool=None,
            cli_flags=cli_flags,
            emitter=None,
            redis=redis,
            keydb=redis,
            aio_connector=connector,
            aiopgpool=None,
            stats_pool=None,
            aiohttpx=aio,
            httpx_session=htx,
            asyncpg_pool=None,
        )

        mel.aiopgpool = mel.driver._pool
        mel.asyncpg = mel.driver._pool
        async with mel as melaniebot:
            log.warning(melaniebot.aiohttpx.connector._resolver)
            log.info(melaniebot.dask)
            await run_bot(melaniebot)


def main():
    build_logger("melanie")
    with asyncio.Runner(loop_factory=loop_factory) as run:
        run.run(bot_main())


if __name__ == "__main__":
    main()
