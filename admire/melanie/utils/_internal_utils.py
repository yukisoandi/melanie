from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional, TypeVar

import pkg_resources
from rich.progress import ProgressColumn, Task
from rich.progress_bar import ProgressBar

__all__ = (
    "safe_delete",
    "fuzzy_command_search",
    "format_fuzzy_results",
    "create_backup",
    "send_to_owners_with_preprocessor",
    "send_to_owners_with_prefix_replaced",
    "expected_version",
    "deprecated_removed",
    "RichIndefiniteBarColumn",
)

_T = TypeVar("_T")


def safe_delete(pth: Path) -> None:
    if pth.exists():
        for root, dirs, files in os.walk(str(pth)):
            os.chmod(root, 0o700)

            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)

            for f in files:
                os.chmod(os.path.join(root, f), 0o700)

        shutil.rmtree(str(pth), ignore_errors=True)


def _fuzzy_log_filter(record):
    return record.funcName != "extractWithoutOrder"


logging.getLogger().addFilter(_fuzzy_log_filter)


async def create_backup(dest: Path = Path.home()) -> Optional[Path]:
    from melaniebot.core import data_manager

    data_path = Path(data_manager.core_data_path().parent)
    if not data_path.exists():
        return None

    dest.mkdir(parents=True, exist_ok=True)
    timestr = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    backup_fpath = dest / f"redv3_{data_manager.instance_name}_{timestr}.tar.gz"
    exclusions = [
        "__pycache__",
        "Lavalink.jar",
        os.path.join("Downloader", "lib"),
        os.path.join("CogManager", "cogs"),
        os.path.join("RepoManager", "repos"),
        os.path.join("Audio", "logs"),
    ]

    # Avoiding circular imports
    from melaniebot.cogs.downloader.repo_manager import RepoManager

    repo_mgr = RepoManager()
    await repo_mgr.initialize()
    repo_output = [{"url": repo.url, "name": repo.name, "branch": repo.branch} for repo in repo_mgr.repos]

    repos_file = data_path / "cogs" / "RepoManager" / "repos.json"
    with repos_file.open("w") as fs:
        json.dump(repo_output, fs, indent=4)
    instance_file = data_path / "instance.json"
    with instance_file.open("w") as fs:
        json.dump({data_manager.instance_name: data_manager.basic_config}, fs, indent=4)
    to_backup = [f for f in data_path.glob("**/*") if all(ex not in str(f) for ex in exclusions) and f.is_file()]

    with tarfile.open(str(backup_fpath), "w:gz") as tar:
        for f in to_backup:
            tar.add(str(f), arcname=str(f.relative_to(data_path)), recursive=False)
    return backup_fpath


# this might be worth moving to `bot.send_to_owners` at later date


def expected_version(current: str, expected: str) -> bool:
    # `pkg_resources` needs a regular requirement string, so "x" serves as requirement's name here
    return current in pkg_resources.Requirement.parse(f"x{expected}")


def deprecated_removed(deprecation_target: str, deprecation_version: str, minimum_days: int, message: str = "", stacklevel: int = 1) -> None:
    warnings.warn(
        f"{deprecation_target} is deprecated since version {deprecation_version} and will be removed in the first minor version that gets released after {minimum_days} days since deprecation. {message}",
        DeprecationWarning,
        stacklevel=stacklevel + 1,
    )


class RichIndefiniteBarColumn(ProgressColumn):
    def render(self, task: Task) -> ProgressBar:
        return ProgressBar(pulse=task.completed < task.total, animation_time=task.get_time(), width=40, total=task.total, completed=task.completed)
