from __future__ import annotations

from typing import Optional


def load_func(payload):
    import io
    import types
    from functools import partial

    import dill

    unpickler = dill.Unpickler(io.BytesIO(payload), fix_imports=True)

    v = unpickler.load()
    code = v["code"]
    func = types.FunctionType(code, globals(), "loadfunc")
    if v["args"]:
        func = partial(func, *v["args"])
    return func


def run_func(func_key: str) -> Optional[dict[str, float]]:  # sourcery skip: extract-method
    import contextlib
    import os
    import pickle
    import signal
    import time

    import psutil
    import redis
    from loguru import logger as log

    try:
        start = time.time()
        final = {"status": None, "exception": None, "result": None, "children": None}
        with redis.from_url(os.getenv("REDIS_URL")) as rediscnx:
            try:
                payload = rediscnx.get(func_key)
                func = load_func(payload)
                rediscnx.delete(func_key)
                if not callable(func):
                    msg = "Func not callable "
                    raise ValueError(msg)
                ran_result = func()
                final.update(status="ok", exception=None, result=ran_result)

            except Exception as e:
                final.update(status="error", exception=e, result=None)
                log.exception("Runner")

            proc = psutil.Process()
            if children := psutil.Process(proc.pid).children(recursive=True):
                final.update(children=len(children))
                for p in children:
                    p: psutil.Process
                    with contextlib.suppress(psutil.NoSuchProcess):
                        p.send_signal(signal.SIGILL)
                        psutil.wait_procs(children, timeout=10)

            final["duration"] = time.time() - start

            rediscnx.set(func_key, pickle.dumps(final, 5, fix_imports=True), ex=300)
            rediscnx.close()
        return final
    except Exception:
        log.exception("Run")


if __name__ == "__main__":
    import fire

    fire.Fire(run_func)
