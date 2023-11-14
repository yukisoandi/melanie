from __future__ import annotations

import regex as re
from regex.regex import Pattern

__all__ = [
    "URL_RE",
    "INVITE_URL_RE",
    "MASS_MENTION_RE",
    "filter_urls",
    "filter_invites",
    "filter_mass_mentions",
    "filter_various_mentions",
    "escape_spoilers",
    "escape_spoilers_and_mass_mentions",
]

# regexes
URL_RE: Pattern[str] = re.compile(r"(https?|s?ftp)://(\S+)", re.I)

INVITE_URL_RE: Pattern[str] = re.compile(r"(discord\.(?:gg|io|me|li)|discord(?:app)?\.com\/invite)\/(\S+)", re.I)

MASS_MENTION_RE: Pattern[str] = re.compile(r"(@)(?=everyone|here)")  # This only matches the @ for sanitizing

OTHER_MENTION_RE: Pattern[str] = re.compile(r"(<)(@[!&]?|#)(\d+>)")

SMART_QUOTE_REPLACEMENT_DICT = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}  # Left single quote  # Right single quote  # Left double quote  # Right double quote

SMART_QUOTE_REPLACE_RE: Pattern[str] = re.compile("|".join(SMART_QUOTE_REPLACEMENT_DICT.keys()))

SPOILER_CONTENT_RE: Pattern[str] = re.compile(r"(?s)(?<!\\)(?P<OPEN>\|{2})(?P<SPOILERED>.*?)(?<!\\)(?P<CLOSE>\|{2})")


# convenience wrappers
def filter_urls(to_filter: str) -> str:
    """Get a string with URLs sanitized.

    This will match any URLs starting with these protocols:

     - ``http://``
     - ``https://``
     - ``ftp://``
     - ``sftp://``

    Parameters
    ----------
    to_filter : str
        The string to filter.

    Returns
    -------
    str
        The sanitized string.

    """
    return URL_RE.sub("[SANITIZED URL]", to_filter)


def filter_invites(to_filter: str) -> str:
    """Get a string with discord invites sanitized.

    Will match any discord.gg, discordapp.com/invite, discord.com/invite, discord.me, or discord.io/discord.li
    invite URL.

    Parameters
    ----------
    to_filter : str
        The string to filter.

    Returns
    -------
    str
        The sanitized string.

    """
    return INVITE_URL_RE.sub("[SANITIZED INVITE]", to_filter)


def filter_mass_mentions(to_filter: str) -> str:
    """Get a string with mass mentions sanitized.

    Will match any *here* and/or *everyone* mentions.

    Parameters
    ----------
    to_filter : str
        The string to filter.

    Returns
    -------
    str
        The sanitized string.

    """
    return MASS_MENTION_RE.sub("@\u200b", to_filter)


def filter_various_mentions(to_filter: str) -> str:
    """Get a string with role, user, and channel mentions sanitized.

    This is mainly for use on user display names, not message content,
    and should be applied sparingly.

    Parameters
    ----------
    to_filter : str
        The string to filter.

    Returns
    -------
    str
        The sanitized string.

    """
    return OTHER_MENTION_RE.sub(r"\1\\\2\3", to_filter)


def escape_spoilers(content: str) -> str:
    """Get a string with spoiler syntax escaped.

    Parameters
    ----------
    content : str
        The string to escape.

    Returns
    -------
    str
        The escaped string.

    """
    return SPOILER_CONTENT_RE.sub(r"\\\g<OPEN>\g<SPOILERED>\\\g<CLOSE>", content)


def escape_spoilers_and_mass_mentions(content: str) -> str:
    """Get a string with spoiler syntax and mass mentions escaped.

    Parameters
    ----------
    content : str
        The string to escape.

    Returns
    -------
    str
        The escaped string.

    """
    return escape_spoilers(filter_mass_mentions(content))
