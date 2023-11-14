import asyncio
import random

import orjson
from fastapi.responses import UJSONResponse
from melanie.helpers import extract_json_tag
from melanie.models.sharedapi.twitter.graphql import TweetEntryItem, UserScreenDataRaw
from melanie.models.sharedapi.twitter.userinfo import TwitterUserDataRaw
from melanie.redis import rcache
from playwright.async_api import Page
from playwright.async_api import Request as PlaywrightRequest

from api_services import services
from core import DEBUG
from routes._base import APIRouter, Query, Request

router = APIRouter(prefix="/api/twitter", tags=["twitter"])
KEY = "gAAAAABk3alE_N-IVn6AEcT26aD1GhH7AjD_1qJBkI3ZUldVnZh6sm0y7a4xkkwMLvlw4Kbp4qYbSev41Ew4G2CaPEgFx93WEw=="


@rcache(ttl="2h", key="twitter_user:{username}")
async def api_fetch_profile(username: str) -> TwitterUserDataRaw:
    result = TwitterUserDataRaw()

    async def handle_resq(r: PlaywrightRequest):
        if "https://twitter.com/i/api/graphql/" not in r.url:
            return
        if "UserTweets" in r.url:
            resp = await r.response()
            if resp:
                data = await resp.body()

                if data:
                    _t = await extract_json_tag(data, "entries", False)
                    if _t:
                        t = orjson.loads(_t)

                        result.tweets = [TweetEntryItem.parse_obj(i) for i in t["entries"]]

        if "UserByScreenName" in r.url:
            resp = await r.response()
            if resp:
                data = await resp.body()
                data = UserScreenDataRaw.parse_raw(data)

                result.info = data.data.user.result
                if result.info.reason:
                    result.tweets = False
                    result.suspended = True

    async with services.page_holder.borrow_page() as page:
        page.on("request", handle_resq)
        try:
            async with asyncio.timeout(20):
                await page.goto(f"https://twitter.com/{username}", wait_until="domcontentloaded")
                content = await page.content()
                if "are protected" in content:
                    result.tweets = False
                if "This account doesn’t exist" in content:
                    return None
                while True:
                    await asyncio.sleep(0.2)
                    if result.info and result.tweets is not None:
                        break
                return result
        except TimeoutError:
            return None
        finally:
            page.remove_listener("request", handle_resq)


async def twitter_login(page: Page):
    await page.goto("https://twitter.com/home")
    password = services.fernet.decrypt(KEY).decode()
    if page.url == "https://twitter.com/i/flow/login":
        await page.get_by_text("Phone, email, or username").fill("monteledwards")
        await page.get_by_text("Next").dispatch_event("click")
        await page.get_by_text("Password", exact=True).fill(password)
        await page.get_by_text("Log in", exact=True).dispatch_event("click")


@router.get("/{username}", description="Fetch a user's profile info and latest tweets", name="Fetch Twitter user", response_model=TwitterUserDataRaw)
async def fetch_twitter_user(request: Request, username: str, force: bool = Query(False, description="Force cache update")):
    async with services.verify_token(request):
        key = f"twitter_profile:{username}"

        async with services.locks[key]:
            if force or DEBUG:
                await services.redis.delete(key)
            cached = await services.redis.get(key)
            if not cached:
                data = await api_fetch_profile(username)
                if not data:
                    return UJSONResponse("Unable to load the profile", 404)
                await services.redis.set(key, data.json(), ex=random.randint(500, 820))
            else:
                data = TwitterUserDataRaw.parse_raw(cached)
            return data
