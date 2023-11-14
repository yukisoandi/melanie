import orjson
from fastapi.responses import RedirectResponse
from melanie import log
from melanie.models import BaseModel
from melanie.models.sharedapi.discord_join import DiscordJoinOauthModel
from yarl import URL

from api_services import services
from routes._base import APIRouter, Request

API_ENDPOINT = "https://discord.com/api/v10"
POST_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
router = APIRouter(prefix="/auth")


class BotOauthConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str | None
    grant_type: str = "authorization_code"


conf: dict[str, BotOauthConfig] = {"melanie": BotOauthConfig(client_id="928394879200034856", client_secret="uMXvNm1eyImUntqN_OdbFrVMm9x9HQEB")}

conf["melanie2"] = BotOauthConfig(client_id="919089251298181181", client_secret="G8q12FmX-sVfu3g7FjmwMXvxeaZewh8C")

conf["melanie3"] = BotOauthConfig(client_id="956298490043060265", client_secret="MCIMSDRY5xHtstw-JCERHwCKd2BKagfV")
conf["melanie1"] = conf["melanie"].copy()


async def exchange_code(code, ident: str, redirect_uri):
    log.info(redirect_uri)
    data = conf[ident].dict()
    data["redirect_uri"] = redirect_uri
    data["code"] = code
    async with services.aio.post("https://discord.com/api/v10/oauth2/token", data=data, headers=POST_HEADERS) as r:
        if not r.ok:
            log.exception(data)
            log.exception(r.headers)
        r.raise_for_status()
        data = await r.text()
        return orjson.loads(data)


def request_get_redirect_uri(request: Request) -> str:
    _url = URL(str(request.url))
    redirect_uri = f"{_url.origin()!s}{_url.path}"
    return redirect_uri.replace("http://", "https://")


@router.get(
    "/{bot_ident}",
    description="Required authorization code grant before a user can add melanie to a server",
    name="Bot Add Authorization",
    include_in_schema=False,
    tags=["auth"],
)
async def discord_exchange(
    request: Request,
    bot_ident: str,
    code: str,
    state: str | None = None,
    guild_id: str | None = None,
    permissions: str | None = None,
) -> DiscordJoinOauthModel:
    return RedirectResponse("https://melaniebot.net")
