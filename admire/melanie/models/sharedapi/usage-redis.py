from __future__ import annotations

from melanie.models.base import BaseModel


class ApiAdminUsage(BaseModel):
    method: str | None
    duration: float | None
    time: float | None
    ts: str | None
    path: str | None
    url: str | None


class ApiInstagramBarackobama(ApiAdminUsage):
    pass


class Model(BaseModel):
    api_admin_usage: list[ApiAdminUsage] | None
    api_instagram_barackobama: list[ApiInstagramBarackobama] | None
