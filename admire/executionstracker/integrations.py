from __future__ import annotations

from discord.http import Route

from melanie import BaseModel


class Account(BaseModel):
    id: str | None
    name: str | None


class User(BaseModel):
    id: str | None
    username: str | None
    avatar: str | None
    discriminator: int | None
    public_flags: int | None
    flags: int | None
    bot: bool | None
    banner: str | None
    accent_color: int | None
    global_name: str | None
    avatar_decoration: None
    display_name: str | None
    banner_color: str | None


class Application(BaseModel):
    id: str | None
    name: str | None
    icon: str | None
    description: str | None
    type: None
    bot: User | None
    summary: str | None


class IntegrationItem(BaseModel):
    type: str | None
    name: str | None
    account: Account | None
    enabled: bool | None
    id: str | None
    application: Application | None
    scopes: list[str] | None
    user: User | None


async def get_integrations(guild_id: int, http) -> list[IntegrationItem]:
    class V9Route(Route):
        BASE: str = "https://discord.com/api/v9"

    r = V9Route("GET", "/guilds/{guild_id}/integrations", guild_id=guild_id)
    data = await http.request(r)
    return [IntegrationItem.parse_obj(i) for i in data]
