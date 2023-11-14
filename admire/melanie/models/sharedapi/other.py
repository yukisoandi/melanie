from __future__ import annotations

import datetime  # noqa
import os
from typing import Any

import arrow
import orjson

from melanie import BaseModel, Field, threaded

DEBUG: str | None = os.getenv("DEBUG")


class ServiceLoginResponse(BaseModel):
    user: str
    token_data: Any | None


def orjson_dumps(obj: bytes) -> bytes:
    if isinstance(obj, str):
        obj = str.encode("UTF-8", "replace")
    if isinstance(obj, str):
        obj = str.encode("UTF-8", "replace")
    return orjson.dumps(obj)


class GitCommitInfo(BaseModel):
    null_hex_sha: str | None = Field(None, alias="NULL_HEX_SHA")
    author_tz_offset: int | None
    authored_date: int | None
    authored_datetime: datetime.datetime | None
    committed_date: int | None
    committed_datetime: datetime.datetime | None
    committer_tz_offset: int | None
    conf_encoding: str | None
    encoding: str | None
    gpgsig: str | None
    hexsha: str | None
    message: str | None
    name_rev: str | None
    size: int | None
    summary: str | None
    type: str | None

    @classmethod
    @threaded
    def from_repo(cls, path=None) -> GitCommitInfo:
        import git

        repo = git.Repo(path)
        cm = repo.head.commit
        data = {name: getattr(cm, name, None) for name in cls.__fields__}
        return cls(**data)

    @staticmethod
    async def current_commit_str() -> str:
        version = await GitCommitInfo.from_repo()
        data = arrow.get(version.committed_datetime).format("MMM D h:mm a")
        return f"commit {version.hexsha[:7]} {version.summary} @ {data}"


class TestUser(BaseModel):
    bio: str | None
    follower_count: str | None = Field(None, alias="follower count")
    username: str | None


class DecodedJwtData(BaseModel):
    aud: str | list[str] | None
    common_name: str | None
    country: str | None
    email: str | None
    exp: int | None
    iat: int | None
    identity_nonce: str | None
    iss: str | None
    nbf: int | None
    sub: str | None
    type: str | None


class KnownAccessServiceToken(BaseModel):
    client_id: str | None
    created_at: datetime.datetime
    duration: str | None
    expires_at: datetime.datetime | None
    id: str | None
    name: str
    updated_at: datetime.datetime
