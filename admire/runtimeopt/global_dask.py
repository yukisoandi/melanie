import distributed

try:
    GLOBAL_DASK  # type: ignore
except NameError:
    GLOBAL_DASK = {}


def get_dask() -> distributed.Client:
    return GLOBAL_DASK.get("client")
