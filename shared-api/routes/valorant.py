import asyncio

import orjson
from aiomisc.backoff import asyncretry
from fastapi.responses import UJSONResponse
from melanie import create_task, log, rcache, snake_cased_dict
from melanie.curl import get_curl
from melanie.models.sharedapi.valorant import StandardProfile, StatsSegment, ValorantAPI2Raw, ValorantDataRaw, ValorantProfileResponse
from melanie.models.sharedapi.valorant2 import MMRData, MmrRaw, UserinfoData, UserInfoRaw
from playwright.async_api import Page
from tornado.escape import url_escape, url_unescape

from api_services import services
from routes._base import APIRouter, Path, Request

router = APIRouter()


def get_overview_stats(data: ValorantDataRaw, username: str) -> tuple[StandardProfile, StatsSegment]:
    username = username.lower()
    overview_stats = next((segment for segment in data.stats.segments if segment.field_key.endswith("playlist||key:competitive,playlist:competitive")), None)
    profile_stats = next((profile for profile in data.stats.standard_profiles if profile.field_key.lower() == f"valorant|riot|{username}"), None)
    return profile_stats, overview_stats.stats if overview_stats else None


@asyncretry(max_tries=3, pause=1)
async def page_eval(page: Page, locator: str):
    data = await page.evaluate(locator)
    if not data:
        msg = "No data"
        raise ValueError(msg)

    return orjson.dumps(orjson.loads(orjson.dumps(data)))


@rcache(ttl="1d", key="fetch_valorant_profile:{username}")
async def fetch_valorant_profile(username: str) -> ValorantDataRaw:
    async with services.page_holder.borrow_page(proxy=True) as page:
        url = f"https://tracker.gg/valorant/profile/riot/{url_escape(username,plus=False)}/overview?season=all"
        log.info("Navigating to {}", url)
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(1)
        data = await page_eval(page, "window.__INITIAL_STATE__")
        data = orjson.loads(data)
        data = snake_cased_dict(data, all_nulls=True)
        data = orjson.dumps(data)
        return ValorantDataRaw.parse_raw(data)


@rcache(ttl="1d", key="fetch_valorant_profile2:{agent}:{tag}")
async def fetch_valorant_profile2(agent, tag) -> ValorantAPI2Raw | None:
    curl = get_curl()
    agent = url_unescape(agent)
    url = f"https://api.henrikdev.xyz/valorant/v1/account/{url_escape(agent,plus=False)}/{tag}"
    log.info(url)
    r = await curl.fetch(url, raise_error=False)
    if r.error:
        if r.code == 404:
            return None
        else:
            raise r.error
    return ValorantAPI2Raw.parse_raw(r.body)


@rcache(ttl="1d", key="fetch_userinfo:{name}:{tag}")
async def fetch_userinfo(name: str, tag: str) -> tuple[MMRData, UserinfoData]:
    aio = services.aio

    url = f"https://api.henrikdev.xyz/valorant/v1/account/{name}/{tag}"
    async with aio.get(url) as r:
        data = await r.read()

        user = UserInfoRaw.parse_raw(data).data

    url = f"https://api.henrikdev.xyz/valorant/v2/by-puuid/mmr/{user.region}/{user.puuid}"
    async with aio.get(url) as r:
        data = await r.read()
        mmr = MmrRaw.parse_raw(data).data
    return mmr, user


@router.get(
    "/api/valorant/{agent}/{tag}",
    name="Get Valorant user",
    tags=["valorant"],
    description="Fetch a Valorant user!",
    response_model_by_alias=False,
    response_model=ValorantProfileResponse,
)
async def get_val_user(
    request: Request,
    agent: str = Path(..., description="Valorant username"),
    tag: str = Path(..., description="Valorant player's tag/discrim"),
):
    username = f"{agent}#{tag}"
    async with services.verify_token(request, description=f"valorant get {username}"):
        profile_task = create_task(fetch_valorant_profile(username))
        api2_task = create_task(fetch_valorant_profile2(agent, tag))
        data: ValorantDataRaw = await profile_task
        if not data:
            return UJSONResponse("Player not found API1", 404)

        api2: ValorantAPI2Raw = await api2_task
        if not api2:
            return UJSONResponse("Player not found on API2", 404)
        profile, overview = get_overview_stats(data, username)

        if not overview:
            return UJSONResponse("This player's profile isnt public. ", 400)

        resp = ValorantProfileResponse()
        resp.peak_rating = overview.peak_rank.metadata.tier_name
        resp.peak_rating_act = overview.peak_rank.metadata.act_name
        resp.current_rating = overview.rank.metadata.tier_name
        resp.kd_ratio = overview.k_d_ratio.value
        resp.damage_round_ratio = overview.damage_per_round.value
        resp.headshot_percent = overview.headshots_percentage.value
        resp.win_percent = overview.matches_win_pct.value
        resp.wins = overview.matches_won.value
        resp.matches_played = overview.matches_played.value
        resp.lost = overview.matches_lost.value
        resp.region = api2.data.region
        resp.kills = overview.kills.value
        resp.deaths = overview.deaths.value

        resp.avatar_url = profile.platform_info.avatar_url
        resp.puuid = api2.data.puuid
        resp.account_level = api2.data.account_level
        resp.name = api2.data.name
        resp.tag = api2.data.tag
        resp.last_update = api2.data.last_update_raw

        return resp
