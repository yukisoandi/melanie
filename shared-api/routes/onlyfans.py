import asyncio

import orjson
from fastapi.responses import ORJSONResponse
from melanie import rcache, snake_cased_dict
from melanie.models.onlyfans import OnlyFansResponse
from melanie.redis import get_redis

from api_services import services
from routes._base import APIRouter, Request

router = APIRouter(tags=["onlyfans"], prefix="/api/onlyfans")
redis = get_redis()


@rcache(ttl="1h")
async def fetch_of_user(username: str) -> bytes:
    async with asyncio.timeout(45), services.page_holder.borrow_page() as page:
        async with page.expect_response(lambda r: f"https://onlyfans.com/api2/v2/users/{username}" in r.url) as ctx_resp:
            url = f"https://onlyfans.com/{username}"
            await page.goto(url)

        response = await ctx_resp.value

        return await response.body()


@router.get(
    "/{username}",
    name="Get Onlyfans",
    description="Fetch an onlyfans user",
    operation_id="getOnlyfans",
    response_model=OnlyFansResponse,
)
async def get_of(request: Request, username: str):
    async with services.verify_token(request), services.locks[f"of:{username}"]:
        result = await fetch_of_user(username)

        data = OnlyFansResponse.parse_obj(snake_cased_dict(orjson.loads(result)))

        if data.error and data.error.code == 0:
            return ORJSONResponse("Invalid user", 404)
        return data
