from __future__ import annotations

import getpass
import sys

import msgpack

from melaniebot.core.drivers.base import ConfigCategory, IdentifierData

from .details import _get_config_details


def _get_config_details():
    unixmsg = (
        ""
        if sys.platform == "win32"
        else (" - Common directories for PostgreSQL Unix-domain sockets (/run/postgresql, /var/run/postgresl, /var/pgsql_socket, /private/tmp, and /tmp),\n")
    )
    host = (
        input(
            f"Enter the PostgreSQL server's address.\nIf left blank, Melanie will try the following, in order:\n - The PGHOST environment variable,\n{unixmsg} - localhost.\n> ",
        )
        or None
    )

    print("Enter the PostgreSQL server port.\nIf left blank, this will default to either:\n - The PGPORT environment variable,\n - 5432.")
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

    return {"host": host, "port": port, "user": user, "password": password, "database": database}


def encode_identifier_data(id_data: IdentifierData) -> tuple[str, str, str, list[str], list[str], int, bool]:
    return (
        id_data.cog_name,
        id_data.uuid,
        id_data.category,
        ["0"] if id_data.category == ConfigCategory.GLOBAL else list(id_data.primary_key),
        list(id_data.identifiers),
        1 if id_data.category == ConfigCategory.GLOBAL else id_data.primary_key_len,
        id_data.is_custom,
    )


def make_ident_key(id_data: IdentifierData) -> bytes:
    encoded = encode_identifier_data(id_data)

    return encoded, msgpack.packb(encoded)
