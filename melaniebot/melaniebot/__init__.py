from __future__ import annotations

import asyncio
import os
import re as _re
import warnings as _warnings
from re import Pattern as _Pattern
from typing import ClassVar as _ClassVar
from typing import Dict as _Dict
from typing import List as _List
from typing import Optional as _Optional
from typing import Tuple as _Tuple
from typing import Union as _Union

import aiohttp
import aiohttp.connector
import aiohttp.resolver
import melanie.curl

setattr(asyncio.sslproto._SSLProtocolTransport, "_start_tls_compatible", True)


aiohttp.resolver.aiodns_default = True
aiohttp.resolver.DefaultResolver = aiohttp.resolver.AsyncResolver
aiohttp.connector.DefaultResolver = aiohttp.resolver.AsyncResolver

aiohttp.resolver.aiodns_default = True
aiohttp.resolver.DefaultResolver = aiohttp.resolver.AsyncResolver


MIN_PYTHON_VERSION = (3, 8, 1)

__all__ = ["MIN_PYTHON_VERSION", "__version__", "version_info", "VersionInfo", "_update_event_loop_policy"]


os.environ["JISHAKU_NO_UNDERSCORE"] = "true"


class VersionInfo:
    ALPHA = "alpha"
    BETA = "beta"
    RELEASE_CANDIDATE = "release candidate"
    FINAL = "final"

    _VERSION_STR_PATTERN: _ClassVar[_Pattern[str]] = _re.compile(
        r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<micro>0|[1-9]\d*)(?:(?P<releaselevel>a|b|rc)(?P<serial>0|[1-9]\d*))?(?:\.post(?P<post_release>0|[1-9]\d*))?(?:\.dev(?P<dev_release>0|[1-9]\d*))?$",
        flags=_re.IGNORECASE,
    )
    _RELEASE_LEVELS: _ClassVar[_List[str]] = [ALPHA, BETA, RELEASE_CANDIDATE, FINAL]
    _SHORT_RELEASE_LEVELS: _ClassVar[_Dict[str, str]] = {"a": ALPHA, "b": BETA, "rc": RELEASE_CANDIDATE}

    def __init__(
        self,
        major: int,
        minor: int,
        micro: int,
        releaselevel: str,
        serial: _Optional[int] = None,
        post_release: _Optional[int] = None,
        dev_release: _Optional[int] = None,
    ) -> None:
        self.major: int = major
        self.minor: int = minor
        self.micro: int = micro

        if releaselevel not in self._RELEASE_LEVELS:
            msg = f"'releaselevel' must be one of: {', '.join(self._RELEASE_LEVELS)}"
            raise TypeError(msg)

        self.releaselevel: str = releaselevel
        self.serial: _Optional[int] = serial
        self.post_release: _Optional[int] = post_release
        self.dev_release: _Optional[int] = dev_release

    @classmethod
    def from_str(cls, version_str: str) -> VersionInfo:
        """Parse a string into a VersionInfo object.

        Raises
        ------
        ValueError
            If the version info string is invalid.

        """
        match = cls._VERSION_STR_PATTERN.match(version_str)
        if not match:
            msg = f"Invalid version string: {version_str}"
            raise ValueError(msg)

        kwargs: _Dict[str, _Union[str, int]] = {key: int(match[key]) for key in ("major", "minor", "micro")}

        releaselevel = match["releaselevel"]
        if releaselevel is not None:
            kwargs["releaselevel"] = cls._SHORT_RELEASE_LEVELS[releaselevel]
        else:
            kwargs["releaselevel"] = cls.FINAL
        for key in ("serial", "post_release", "dev_release"):
            if match[key] is not None:
                kwargs[key] = int(match[key])
        return cls(**kwargs)

    @classmethod
    def from_json(cls, data: _Union[_Dict[str, _Union[int, str]], _List[_Union[int, str]]]) -> VersionInfo:
        return cls(*data) if isinstance(data, _List) else cls(**data)

    def to_json(self) -> _Dict[str, _Union[int, str]]:
        return {
            "major": self.major,
            "minor": self.minor,
            "micro": self.micro,
            "releaselevel": self.releaselevel,
            "serial": self.serial,
            "post_release": self.post_release,
            "dev_release": self.dev_release,
        }

    def _generate_comparison_tuples(self, other: VersionInfo) -> _List[_Tuple[int, int, int, int, _Union[int, float], _Union[int, float], _Union[int, float]]]:
        return [
            (
                obj.major,
                obj.minor,
                obj.micro,
                obj._RELEASE_LEVELS.index(obj.releaselevel),
                obj.serial if obj.serial is not None else 0,
                obj.post_release if obj.post_release is not None else -0,
                obj.dev_release if obj.dev_release is not None else 0,
            )
            for obj in (self, other)
        ]

    def __lt__(self, other: VersionInfo) -> bool:
        tups = self._generate_comparison_tuples(other)
        return tups[0] < tups[1]

    def __eq__(self, other: VersionInfo) -> bool:
        tups = self._generate_comparison_tuples(other)
        return tups[0] == tups[1]

    def __le__(self, other: VersionInfo) -> bool:
        tups = self._generate_comparison_tuples(other)
        return tups[0] <= tups[1]

    def __str__(self) -> str:
        ret = f"{self.major}.{self.minor}.{self.micro}"
        if self.releaselevel != self.FINAL:
            short = next(k for k, v in self._SHORT_RELEASE_LEVELS.items() if v == self.releaselevel)
            ret += f"{short}{self.serial}"
        if self.post_release is not None:
            ret += f".post{self.post_release}"
        if self.dev_release is not None:
            ret += f".dev{self.dev_release}"
        return ret

    def __repr__(self) -> str:
        return "VersionInfo(major={major}, minor={minor}, micro={micro}, releaselevel={releaselevel}, serial={serial}, post={post_release}, dev={dev_release})".format(
            **self.to_json(),
        )


__version__ = "3.9.1.dev2"
version_info = VersionInfo.from_str(__version__)
_warnings.filterwarnings("ignore", category=DeprecationWarning)
_warnings.filterwarnings("ignore", category=UserWarning)
_warnings.filterwarnings("ignore", module=r"fuzzywuzzy.*")
_warnings.filterwarnings("ignore", category=DeprecationWarning, module="importlib", lineno=219)
_warnings.filterwarnings("ignore", category=DeprecationWarning, module="asyncio", message="The loop argument is deprecated since Python 3.8")
