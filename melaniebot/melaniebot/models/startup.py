from __future__ import annotations

import asyncio
from typing import Any, Optional

from melanie import BaseModel


class StartupOptions(BaseModel, arbitrary_types_allowed=True):
    instance_name: str
    loop: asyncio.AbstractEventLoop = None
    token: str = None
    owner: int = None
    co_owner: list = []
    logging_level: int = 20
    copy_data: bool = False
    debuginfo: bool = False
    dev: bool = False
    disable_intent: list = []
    dry_run: bool = False
    edit: bool = False
    edit_data_path: Optional[Any] = None
    edit_instance_name: Optional[Any] = None
    list_instances: bool = False
    load_cogs: list[str] = []
    mentionable: bool = False
    message_cache_size: int = 5000
    no_cogs: bool = False
    no_instance: bool = False
    no_message_cache: bool = False
    no_prompt: bool = False
    overwrite_existing_instance: bool = False
    prefix: list = []
    rich_logging: Optional[Any] = None
    rich_traceback_extra_lines: int = 0
    rich_traceback_show_locals: bool = False
    rpc: bool = False
    rpc_port: int = 6133
    use_team_features: bool = True
    version: bool = False
