from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Union, cast

from loguru import logger as log

from melaniebot import VersionInfo
from melaniebot import version_info as red_version_info

from . import installable

if TYPE_CHECKING:
    from .json_mixins import RepoJSONMixin

__all__ = ("REPO_SCHEMA", "INSTALLABLE_SCHEMA", "update_mixin")


class UseDefault:
    """To be used as sentinel."""


# sentinel value
USE_DEFAULT = UseDefault()


def ensure_tuple_of_str(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> tuple[str, ...]:
    default: tuple[str, ...] = ()
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, list):
        log.warning("Invalid value of '{}' key (expected list, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    for item in value:
        if not isinstance(item, str):
            log.warning("Invalid item in '{}' list (expected str, got {}) in JSON information file at path: {}", key_name, type(item).__name__, info_file)
            return default
    return tuple(value)


def ensure_str(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> str:
    default = ""
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, str):
        log.warning("Invalid value of '{}' key (expected str, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    return value


def ensure_red_version_info(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> VersionInfo:
    default = red_version_info
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, str):
        log.warning("Invalid value of '{}' key (expected str, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    try:
        version_info = VersionInfo.from_str(value)
    except ValueError:
        log.warning("Invalid value of '{}' key (given value isn't a valid version string) in JSON information file at path: {}", key_name, info_file)
        return default
    return version_info


def ensure_python_version_info(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> tuple[int, int, int]:
    default = (3, 5, 1)
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, list):
        log.warning("Invalid value of '{}' key (expected list, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    count = len(value)
    if count != 3:
        log.warning("Invalid value of '{}' key (expected list with 3 items, got {} items) in JSON information file at path: {}", key_name, count, info_file)
        return default
    for item in value:
        if not isinstance(item, int):
            log.warning("Invalid item in '{}' list (expected int, got {}) in JSON information file at path: {}", key_name, type(item).__name__, info_file)
            return default
    return cast(tuple[int, int, int], tuple(value))


def ensure_bool(info_file: Path, key_name: str, value: Union[Any, UseDefault], *, default: bool = False) -> bool:
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, bool):
        log.warning("Invalid value of '{}' key (expected bool, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    return value


def ensure_required_cogs_mapping(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> dict[str, str]:
    default: dict[str, str] = {}
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, dict):
        log.warning("Invalid value of '{}' key (expected dict, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default
    # keys in json dicts are always strings
    for item in value.values():
        if not isinstance(item, str):
            log.warning("Invalid item in '{}' dict (expected str, got {}) in JSON information file at path: {}", key_name, type(item).__name__, info_file)
            return default
    return value


def ensure_installable_type(info_file: Path, key_name: str, value: Union[Any, UseDefault]) -> installable.InstallableType:
    default = installable.InstallableType.COG
    if value is USE_DEFAULT:
        return default
    if not isinstance(value, str):
        log.warning("Invalid value of '{}' key (expected str, got {}) in JSON information file at path: {}", key_name, type(value).__name__, info_file)
        return default  # NOTE: old behavior was to use InstallableType.UNKNOWN
    if value in ("", "COG"):
        return installable.InstallableType.COG
    if value == "SHARED_LIBRARY":
        return installable.InstallableType.SHARED_LIBRARY
    return installable.InstallableType.UNKNOWN


EnsureCallable = Callable[[Path, str, Union[Any, UseDefault]], Any]
SchemaType = dict[str, EnsureCallable]

REPO_SCHEMA: SchemaType = {"author": ensure_tuple_of_str, "description": ensure_str, "install_msg": ensure_str, "short": ensure_str}
INSTALLABLE_SCHEMA: SchemaType = {
    "min_bot_version": ensure_red_version_info,
    "max_bot_version": ensure_red_version_info,
    "min_python_version": ensure_python_version_info,
    "hidden": ensure_bool,
    "disabled": ensure_bool,
    "required_cogs": ensure_required_cogs_mapping,
    "requirements": ensure_tuple_of_str,
    "tags": ensure_tuple_of_str,
    "type": ensure_installable_type,
    "end_user_data_statement": ensure_str,
}


def update_mixin(repo_or_installable: RepoJSONMixin, schema: SchemaType) -> None:
    info = repo_or_installable._info
    info_file = repo_or_installable._info_file
    for key, callback in schema.items():
        setattr(repo_or_installable, key, callback(info_file, key, info.get(key, USE_DEFAULT)))
