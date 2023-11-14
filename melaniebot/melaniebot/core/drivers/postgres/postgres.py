from __future__ import annotations

import getpass
import os
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable, Optional, Union

import asyncpg
import asyncpg.connection
import orjson
from melanie import capturetime

from melaniebot.core import errors
from melaniebot.core.drivers.base import BaseDriver, ConfigCategory, IdentifierData

__all__ = ["PostgresDriver"]

_PKG_PATH = Path(__file__).parent
DDL_SCRIPT_PATH = _PKG_PATH / "ddl.sql"
DROP_DDL_SCRIPT_PATH = _PKG_PATH / "drop_ddl.sql"


# Inhibit connection reset upon release to connection pool in order to avoid


def encode_identifier_data(
    id_data: IdentifierData,
) -> tuple[str, str, str, list[str], list[str], int, bool]:
    return (
        id_data.cog_name,
        id_data.uuid,
        id_data.category,
        ["0"] if id_data.category == ConfigCategory.GLOBAL else list(id_data.primary_key),
        list(id_data.identifiers),
        1 if id_data.category == ConfigCategory.GLOBAL else id_data.primary_key_len,
        id_data.is_custom,
    )


class PostgresDriver(BaseDriver):
    _pool: Optional[asyncpg.pool.Pool] = None
    log: bool = os.getenv("LOG_QUERY")

    @classmethod
    async def initialize(cls, **storage_details) -> None:
        if asyncpg is None:
            msg = "Melanie must be installed with the [postgres] extra to use the PostgreSQL driver"
            raise errors.MissingExtraRequirements(msg)
        cls._pool = await asyncpg.create_pool(**storage_details)
        with DDL_SCRIPT_PATH.open() as fs:
            await cls._pool.execute(fs.read())

    @classmethod
    async def teardown(cls) -> None:
        if cls._pool is not None:
            await cls._pool.close()

    async def clear_cache():
        pass

    @staticmethod
    def get_config_details():
        unixmsg = (
            ""
            if sys.platform == "win32"
            else (
                " - Common directories for PostgreSQL Unix-domain sockets (/run/postgresql, /var/run/postgresl, /var/pgsql_socket, /private/tmp, and /tmp),\n"
            )
        )
        host = (
            input(
                f"Enter the PostgreSQL server's address.\nIf left blank, Melanie will try the following, in order:\n - The PGHOST environment variable,\n{unixmsg} - localhost.\n> ",
            )
            or None
        )

        print(
            "Enter the PostgreSQL server port.\nIf left blank, this will default to either:\n - The PGPORT environment variable,\n - 5432.",
        )
        while True:
            port = input("> ") or None
            if port is None:
                break

            try:
                port = int(port)
            except ValueError:
                print("Port must be a number")
            else:
                break

        user = (
            input(
                "Enter the PostgreSQL server username.\nIf left blank, this will default to either:\n - The PGUSER environment variable,\n - The OS name of the user running Melanie (ident/peer authentication).\n> ",
            )
            or None
        )

        passfile = r"%APPDATA%\postgresql\pgpass.conf" if sys.platform == "win32" else "~/.pgpass"
        password = getpass.getpass(
            f"Enter the PostgreSQL server password. The input will be hidden.\n  NOTE: If using ident/peer authentication (no password), enter NONE.\nWhen NONE is entered, this will default to:\n - The PGPASSWORD environment variable,\n - Looking up the password in the {passfile} passfile,\n - No password.\n> ",
        )
        if password == "NONE":
            password = None

        database = (
            input(
                "Enter the PostgreSQL database's name.\nIf left blank, this will default to either:\n - The PGDATABASE environment variable,\n - The OS name of the user running Melanie.\n> ",
            )
            or None
        )

        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
        }

    async def get(self, identifier_data: IdentifierData):
        result = await self._execute(
            "SELECT red_config.get($1)",
            encode_identifier_data(identifier_data),
            method=self._pool.fetchval,
        )
        if result is None:
            # The result is None both when postgres yields no results, or when it yields a NULL row
            # A 'null' JSON value would be returned as encoded JSON, i.e. the string 'null'
            raise KeyError
        return orjson.loads(result)

    async def set(self, identifier_data: IdentifierData, value=None):
        try:
            await self._execute(
                "SELECT red_config.set($1, $2::jsonb)",
                encode_identifier_data(identifier_data),
                orjson.dumps(value).decode("UTF-8"),
            )
        except asyncpg.ErrorInAssignmentError as e:
            raise errors.CannotSetSubfield from e

    async def clear(self, identifier_data: IdentifierData):
        await self._execute(
            "SELECT red_config.clear($1)",
            encode_identifier_data(identifier_data),
        )

    async def inc(
        self,
        identifier_data: IdentifierData,
        value: Union[int, float],
        default: Union[int, float],
    ) -> Union[int, float]:
        try:
            return await self._execute(
                "SELECT red_config.inc($1, $2, $3)",
                encode_identifier_data(identifier_data),
                value,
                default,
                method=self._pool.fetchval,
            )

        except asyncpg.WrongObjectTypeError as exc:
            raise errors.StoredTypeError(*exc.args) from exc

    async def toggle(self, identifier_data: IdentifierData, default: bool) -> bool:
        try:
            return await self._execute(
                "SELECT red_config.inc($1, $2)",
                encode_identifier_data(identifier_data),
                default,
                method=self._pool.fetchval,
            )
        except asyncpg.WrongObjectTypeError as exc:
            raise errors.StoredTypeError(*exc.args) from exc

    @classmethod
    async def aiter_cogs(cls) -> AsyncIterator[tuple[str, str]]:
        query = "SELECT cog_name, cog_id FROM red_config.red_cogs"
        async with cls._pool.acquire() as conn, conn.transaction():
            async for row in conn.cursor(query):
                yield row["cog_name"], row["cog_id"]

    @classmethod
    async def delete_all_data(cls, *, drop_db: Optional[bool] = None, **kwargs) -> None:
        """Delete all data being stored by this driver.

        Schemas within the database which
        store bot data will be dropped, as well as functions,
        aggregates, event triggers, and meta-tables.

        Parameters
        ----------
        drop_db : Optional[bool]
            If set to ``True``, function will print information
            about not being able to drop the entire database.

        """
        if drop_db is True:
            print(
                "Dropping the entire database is not possible in PostgreSQL driver. We will delete all of Melanie's data within this database, without dropping the database itself.",
            )
        with DROP_DDL_SCRIPT_PATH.open() as fs:
            await cls._pool.execute(fs.read())

    @classmethod
    async def _execute(
        cls,
        query: str,
        *args,
        method: Optional[Callable] = None,
    ) -> Any:
        if method is None:
            method = cls._pool.execute
        if not cls.log:
            return await method(query, *args)
        with capturetime(textwrap.shorten(f"Query {query}, Args: {args}", width=3000)):
            return await method(query, *args)
