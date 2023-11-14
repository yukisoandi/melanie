from __future__ import annotations

import logging
import queue
import sys
import threading
import warnings

from loguru import logger

from runtimeopt import DEBUG

from .intercept_handler import InterceptHandler


class AsyncLogEmitter(object):
    def __init__(self) -> None:
        self.intercept_handler = InterceptHandler()
        self.thread = threading.Thread(target=self.runner, daemon=True, name="loggerThread")
        self.queue = queue.Queue()

        self.shutdown_event = threading.Event()
        self.thread.start()

    def runner(self) -> None:
        while True:
            if self.shutdown_event.is_set():
                return
            try:
                msg: str = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            sys.__stderr__.buffer.write(msg.encode("UTF-8"))
            sys.__stderr__.buffer.flush()
            self.queue.task_done()

    def emit(self, msg) -> None:
        self.queue.put_nowait(msg)


def build_logger(*a, **ka) -> AsyncLogEmitter:
    import distributed.versions

    warnings.simplefilter("ignore", distributed.versions.VersionMismatchWarning, append=True)
    LOG_LEVEL = "DEBUG" if DEBUG else "INFO"
    emitter = AsyncLogEmitter()
    handlers = [
        {
            "sink": emitter.emit,
            "colorize": True,
            "backtrace": True,
            "enqueue": False,
            "diagnose": True,
            "level": LOG_LEVEL,
            "catch": True,
            "format": "<le>{time:HH:mm:ss.SSS}</le>|<ly>{thread.name}</ly> |<level>{level:<7}</level>|<cyan>{name}</cyan>(<cyan>{function}</cyan>:<cyan>{line}</cyan>) <level>{message}</level>",
        },
    ]

    logger.configure(handlers=handlers)
    logger.level(name="DEBUG", color="<magenta>")
    logging.basicConfig(handlers=[emitter.intercept_handler], level=LOG_LEVEL, force=True)
    logging.captureWarnings(True)
    logger.disable("distributed.utils")
    warnings.simplefilter("ignore", DeprecationWarning, append=True)
    logging.getLogger("distributed.scheduler").setLevel("ERROR")
    for name in ["hpack.hpack", ""]:
        _log = logging.getLogger(name)
        _log.disabled = True
    logger.disable("hpack.hpack")
    return emitter


make_dask_sink = build_logger
