from __future__ import annotations

from contextlib import suppress
from typing import Any

import discord  # noqa

from melanie import BaseModel


class CachedAuditEntry(BaseModel):
    action: str
    after_values: dict[str, str | dict[Any, Any]] = {}
    before_values: dict[str, str | dict[Any, Any]] = {}
    category: str | None  #
    created_at: str
    extra: str
    id: int
    reason: str
    target: str
    user: str

    @classmethod
    def from_audit_log(cls, audit: discord.AuditLogEntry) -> CachedAuditEntry:
        e = CachedAuditEntry(
            action=str(audit.action).replace("AuditLogAction", ""),
            user=str(audit.user),
            target=str(audit.target),
            reason=str(audit.reason),
            id=audit.id,
            extra=str(audit.extra),
            created_at=str(audit.created_at),
        )

        def format_role(r):
            if isinstance(r, list):
                return [{"id": r.id, "name": r.name, "permissions": getattr(getattr(r, "permissions", None), "value", None)} for r in r]

            return {"id": r.id, "name": r.name, "permissions": getattr(getattr(r, "permissions", None), "value", None)}

        with suppress(AttributeError):
            str(audit.category)
        for attr, v in audit.before:
            e.before_values[attr] = str(v)
        for attr, v in audit.after:
            v = format_role(v) if attr == "roles" else str(v)
            e.after_values[attr] = v
        return e
