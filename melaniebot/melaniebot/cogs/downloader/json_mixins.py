from __future__ import annotations

from pathlib import Path
from typing import Any

import ujson
from loguru import logger as log

from .info_schemas import REPO_SCHEMA, update_mixin


class RepoJSONMixin:
    INFO_FILE_NAME = "info.json"

    def __init__(self, repo_folder: Path) -> None:
        self._repo_folder = repo_folder

        self.author: tuple[str, ...]
        self.install_msg: str
        self.short: str
        self.description: str

        self._info_file = repo_folder / self.INFO_FILE_NAME
        self._info: dict[str, Any]

        self._read_info_file()

    def _read_info_file(self) -> None:
        if self._info_file.exists():
            try:
                with self._info_file.open(encoding="utf-8") as f:
                    info = ujson.load(f)
            except ValueError:
                log.exception("Invalid JSON information file at path: {}", self._info_file)
                info = {}
        else:
            info = {}
        if not isinstance(info, dict):
            log.warning("Invalid top-level structure (expected dict, got {}) in JSON information file at path: {}", type(info).__name__, self._info_file)
            info = {}
        self._info = info

        update_mixin(self, REPO_SCHEMA)
