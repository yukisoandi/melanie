from typing import Optional

import arrow
from arrow import Arrow
from pydantic import Field

from melanie import BaseModel


class UserSale(BaseModel):
    method: Optional[str]
    date: Optional[float]
    guild_id: Optional[int]
    guild_name: Optional[str]
    user_name: Optional[str]
    monthly: bool = False
    created_by: int = Field(None, description="The User ID of the staff member that created the sale entry")

    @property
    def created_at(self) -> Arrow:
        return arrow.get(self.date)


class UserSettings(BaseModel):
    purchases: list[UserSale] = []

    def get_sale(self, guild_id: int) -> None | UserSale:
        return next(filter(lambda x: x.guild_id == guild_id, self.purchases), None)

    @property
    def last_purchase(self) -> UserSale | None:
        if self.purchases:
            _sorted = sorted(self.purchases, key=lambda x: x.created_at, reverse=True)
            return _sorted[0]


class GlobalSettings(BaseModel):
    paid_role_id: int = None
