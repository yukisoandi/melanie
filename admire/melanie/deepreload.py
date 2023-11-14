from __future__ import annotations

from types import ModuleType

from loguru import logger


def _reload(module: ModuleType, reload_all, reloaded) -> None:
    from importlib import import_module, reload

    if isinstance(module, ModuleType):
        module_name = module.__name__
    elif isinstance(module, str):
        module_name, module = module, import_module(module)
    else:
        msg = f"'module' must be either a module or str; got: {module.__class__.__name__}"
        raise TypeError(msg)

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if check := (
            # is it a module?
            isinstance(attr, ModuleType)
            # has it already been reloaded?
            and attr.__name__ not in reloaded
            # is it a proper submodule? (or just reload all)
            and (reload_all or attr.__name__.startswith(module_name))
        ):
            _reload(attr, reload_all, reloaded)

    logger.warning(f"reloading module: {module.__name__}")
    reload(module)
    reloaded.add(module_name)


def deepreload(module: ModuleType, reload_external_modules: bool = False) -> None:
    """Recursively reload a module (in order of dependence).

    Parameters
    ----------
    module : ModuleType or str
        The module to reload.

    reload_external_modules : bool, optional

        Whether to reload all referenced modules, including external ones which
        aren't submodules of ``module``.

    """
    _reload(module, reload_external_modules, set())


def reload_module_dask(mod: str) -> bool:
    import importlib

    from melanie.deepreload import deepreload

    try:
        mod = importlib.import_module(mod)
    except ModuleNotFoundError:
        return False

    deepreload(mod)

    return True
