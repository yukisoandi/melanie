import os

os.environ["CURL_UA"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36"
import asyncio

from melanie.redis import MelanieRedis
from runtimeopt import DEBUG, loop_factory
from runtimeopt.preload_logger import build_logger
from uvicorn import Server
from uvicorn.config import Config


async def run_server() -> None:
    async with await MelanieRedis.from_url():
        from launch import app

        PORT = 8091 if DEBUG else 8099
        s = Server(
            Config(
                app,
                uds=None if DEBUG else "api.sock",
                port=PORT,
                host="0.0.0.0",
                log_config={"version": 1, "disable_existing_loggers": False, "loggers": {"uvicorn": {"level": "DEBUG"}}},
                access_log=False,
            ),
        )
        await s.serve()


def run():
    build_logger("api")
    with asyncio.Runner(loop_factory=loop_factory, debug=DEBUG) as run:
        run.run(run_server())


if __name__ == "__main__":
    run()
