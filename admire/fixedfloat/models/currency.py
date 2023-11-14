#   timestamp: 2023-02-18T19:48:56+00:00

from __future__ import annotations

from melanie import BaseModel


class FixedFloatCurrency(BaseModel):
    currency: str
    symbol: str
    network: str
    sub: str
    name: str
    alias: str
    type: str
    precision: str
    send: str
    recv: str
