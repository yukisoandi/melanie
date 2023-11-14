import asyncio
import random
from collections import defaultdict
from contextlib import suppress
from functools import partial

import orjson
import pydantic
import regex as re
import yarl
from boltons.iterutils import unique
from boltons.urlutils import find_all_links
from fastapi.responses import UJSONResponse
from melanie import alru_cache, get_curl, get_redis, log, snake_cased_dict, url_to_mime
from melanie.core import spawn_task
from melanie.models.sharedapi.aweme2 import TikTokAwmeRaw
from melanie.models.sharedapi.tiktok import TikTokUserProfileResponse
from melanie.models.sharedapi.tiktok_items import ItemList, TikTokItemListRaw, TiktokTopUserVideoResults, TikTokTopVideoItem
from melanie.models.sharedapi.tiktok_sigistate import ItemModule, TikTokSigiStateRaw
from melanie.redis import rcache
from playwright.async_api import Request as PlaywrightRequest
from regex.regex import Pattern
from tiktok.models.api_response import TiktokPostRequest, TikTokVideoResponse
from tornado.httputil import url_concat
from yt_dlp import YoutubeDL
from yt_dlp.extractor.tiktok import TikTokBaseIE

from api_services import api_username_var, services
from core import media_url_from_request
from routes._base import APIRouter, Request

AWME_RE: Pattern[str] = re.compile(r"https?://www\.tiktok\.com/(?:embed|@(?P<user_id>[\w\.-]+)/video)/(?P<id>\d+)")

router = APIRouter(prefix="/api/tiktok", tags=["tiktok"])

ctx_sems = defaultdict(partial(asyncio.BoundedSemaphore, 5))
redis = get_redis()

yt = YoutubeDL({"simulate": True, "clean_infojson": True, "consoletitle": False})

yt.add_default_info_extractors()

TT: TikTokBaseIE = yt.get_info_extractor("TikTok")
YT_LOCK = asyncio.Lock()


def ttl_30min_1h(*a, **ka) -> int:
    return random.randint(1800, 3600)


def validate_tiktok_username(name: str) -> str:
    name = name.replace("'b", "")
    name = name.replace("'", "")
    if len(name) > 24:
        msg = "Usernames must be less than 24 characters"
        raise ValueError(msg)
    name = str(name).removeprefix("@")
    allowed_chars = ("_", ".")
    if name.endswith("."):
        msg = "Usernames cannot end with periods"
        raise ValueError(msg)
    for c in name:
        if not c.isalnum() and c not in allowed_chars:
            msg = f"{c} is not allowed in TikTok usernames"
            raise ValueError(msg)
    return name


async def parse_aweme_formats(data: dict):
    async with YT_LOCK:
        return TT._parse_aweme_video_app(data)


def check_message(content: str):
    links = find_all_links(content)
    url = next((x for x in links if "tiktok.com" in x.host), None)
    return url or None


def extract_aweme_pair(url) -> tuple[str, str]:
    url = str(url)
    user_id, aweme_id = AWME_RE.match(url).groups()
    return user_id, aweme_id


def find_jpeg(list_of_urls) -> str | None:
    return next(
        (x for x in list_of_urls if yarl.URL(x).suffix in (".jpeg", ".jpg", ".png")),
        None,
    )


@alru_cache(maxsize=None)
async def fetch_aweme(aweme):
    url = url_concat(
        "https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/",
        {
            "aweme_id": str(aweme),
            "version_name": "26.1.3",
            "version_code": "260103",
            "build_number": "26.1.3",
            "manifest_version_code": "260103",
            "update_version_code": "260103",
            "openudid": "b58e9595a7964de6",
            "uuid": "8675991749151627",
            "_rticket": "1674158727049",
            "ts": "1674158727",
            "device_brand": "Google",
            "device_type": "Pixel 4",
            "device_platform": "android",
            "resolution": "1080*1920",
            "dpi": "420",
            "os_version": "10",
            "os_api": "29",
            "carrier_region": "US",
            "sys_region": "US",
            "region": "US",
            "app_name": "trill",
            "app_language": "en",
            "language": "en",
            "timezone_name": "America/New_York",
            "timezone_offset": "-14400",
            "channel": "googleplay",
            "ac": "wifi",
            "mcc_mnc": "310260",
            "is_my_cn": "0",
        },
    )
    curl = get_curl()
    r = await curl.fetch(url)
    return snake_cased_dict(orjson.loads(r.body))


@alru_cache(maxsize=None)
async def resolve_redirect(url):
    async with services.htx.stream("GET", url, follow_redirects=False) as r:
        location = r.headers.get("location")
        if location:
            log.success("Resolved redirect to target {} from {}", location, url)
        return location or url


async def fetch_video_sigi(author_id, video_id):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    async with services.aio(f"https://www.tiktok.com/@{author_id}/video/{video_id}", headers=headers) as r:
        r.ra
        await r.text("UTF-8", "replace")


@alru_cache
async def api_fetch_post(url):
    url = check_message(url)
    url = str(url)
    awme_data = None
    vid = None
    video_task = None
    if not url:
        return UJSONResponse("invalid post url - fails first check", 400)
    try:
        uid, awme = extract_aweme_pair(url)
    except AttributeError:
        url = await resolve_redirect(url)
        try:
            uid, awme = extract_aweme_pair(url)
        except AttributeError:
            return None
    data = await fetch_aweme(awme)
    awme_data = next(filter(lambda a: a["aweme_id"] == str(awme), data["aweme_list"]), None)
    if not data or not awme_data:
        return None
    _awme_data = awme_data
    if "author" in awme_data:
        awme_data["author"]["unique_id"] = uid
    awme_data["yt_info"] = await parse_aweme_formats(_awme_data)
    data = orjson.dumps(awme_data)
    vid = TikTokAwmeRaw.parse_raw(data)
    if vid.author.avatar_thumb and vid.author.avatar_thumb.url_list:
        avatar_url = vid.author.avatar_thumb.url_list[0]
    vid.author.avatar_thumb = avatar_url
    dataset = vid.dict()
    if vid.statistics:
        dataset.update(vid.statistics.dict())
    if vid.share_info:
        dataset.update(vid.share_info.dict())
    res = TikTokVideoResponse.parse_obj(dataset)
    if vid and vid.video and vid.video.dynamic_cover and vid.video.dynamic_cover.url_list:
        res.cover_image_url = vid.video.dynamic_cover.url_list[0]
    if targetformats := [i for i in vid.yt_info.formats if "watermark" not in i.format_note.lower() and i.vcodec == "h264"] or [
        i for i in vid.yt_info.formats if "watermark" not in i.format_note.lower()
    ]:
        vid.yt_info.formats = targetformats
        res.direct_download_urls = [str(i.url) for i in sorted(vid.yt_info.formats, key=lambda x: x.source_preference, reverse=False)]
        target_url = None
        if vid.video.play_addr_h264 and vid.video.play_addr_h264.url_list:
            target_url = vid.video.play_addr_h264.url_list[0]

            filename, video_task = services.insta.start_render(target_url, "TikTok", prekey=str(res.aweme_id), suffix=".mp4")
            res.direct_download_urls = [media_url_from_request(filename, direct=True)]
            res.video_url = res.direct_download_urls[0]
            res.filename = filename

        elif not target_url and res.direct_download_urls:
            target_url = res.direct_download_urls[0]
            filename, video_task = services.insta.start_render(target_url, "TikTok", prekey=str(res.aweme_id), suffix=".mp4")
            res.direct_download_urls = [media_url_from_request(filename, direct=True)]
            res.video_url = res.direct_download_urls[0]
            res.filename = filename
    if res.image_post_info and res.image_post_info.images:
        for i in res.image_post_info.images:
            if img_url := find_jpeg(i.display_image.url_list):
                filename, task = services.insta.start_render(img_url, "TikTok", suffix=".jpg")
                i.display_image.url_list = [media_url_from_request(filename, direct=True)]

    if res.avatar_thumb:
        _search = url_to_mime(res.avatar_thumb)
        _suffix = _search[1] if _search[0] else ".jpg"
        filename, task = services.insta.start_render(res.avatar_thumb, "TikTok", suffix=_suffix)
        res.avatar_thumb = media_url_from_request(filename, direct=True)
    if res.author:
        res.author.unique_id = vid.author.unique_id
        if res.author.avatar_thumb:
            _search = url_to_mime(res.author.avatar_thumb)
            _suffix = _search[1] if _search[0] else ".jpg"
            filename, task = services.insta.start_render(res.author.avatar_thumb, "TikTok", suffix=_suffix)
            res.author.avatar_thumb = media_url_from_request(filename, direct=True)
            res.avatar_thumb = res.author.avatar_thumb
    if vid.video and vid.video.origin_cover and vid.video.origin_cover.url_list and (img_url := find_jpeg(vid.video.origin_cover.url_list)):
        filename, task = services.insta.start_render(img_url, "TikTok")
        res.cover_image_url = media_url_from_request(filename, direct=True)
    if not res.desc:
        res.desc = ""
    res.desc = res.desc.replace("##", "#")
    res.id = res.aweme_id
    res.author_id = res.author.unique_id
    res.share_url = str(yarl.URL(f"https://www.tiktok.com/@{res.author.unique_id}/video/{res.aweme_id}"))

    return res


@router.post("/post", name="Download TikTok Post", response_model=TikTokVideoResponse, description="Download a TikTok post and its associated metadata.")
async def fetch_tiktok_post(post_request: TiktokPostRequest, request: Request):
    async with services.verify_token(request, description=post_request.url):
        username = api_username_var.get()
        url = str(post_request.url)

        async with ctx_sems[f"post:{username}"], asyncio.timeout(60):
            return await api_fetch_post(url) or UJSONResponse("Invalid post", 404)


@rcache(ttl="1h", key="tiktok_user_sigi:{username}", lock=True)
async def fetch_user_sigi_state(username: str) -> TikTokSigiStateRaw:
    await asyncio.sleep(0.001)
    username = username.replace("@", "")
    async with services.page_holder.borrow_page(proxy=True) as page:
        await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
        _value = await page.evaluate("SIGI_STATE")
        try:
            if not _value:
                return log.warning("Sigi state was empty for {}", username)
            value = orjson.dumps(_value)
            return TikTokSigiStateRaw.parse_raw(value)
        except pydantic.ValidationError:
            log.warning("Unable to parse SIGI_STATE {}", username)
            return None
        except TimeoutError:
            log.warning("Timeout SIGI_STATE {}", username)
        return None


@rcache(ttl="4h", key="tiktok:profile:{username}")
async def get_tiktok_profile(username: str) -> TikTokUserProfileResponse | None | bool:
    username = username.lower()
    data = await fetch_user_sigi_state(username)
    if not data:
        return None
    if not data.user_module:
        return None

    try:
        user = data.user_module.users[username]
        stats = data.user_module.stats[username]
        _user = user.dict(exclude_none=True)
    except KeyError:
        log.warning("Unable to find user in the sigistate {} ", username)
        return None
    for k in list(_user.keys()):
        if "avatar" in k:
            del _user[k]
    resp = TikTokUserProfileResponse(**_user)
    filename, task = services.insta.start_render(user.avatar_larger or user.avatar_medium, "TikTok", suffix=".jpg")
    resp.avatar_url = media_url_from_request(filename)
    resp.digg_count = stats.digg_count
    resp.follower_count = stats.follower_count
    resp.following_count = stats.following_count
    resp.heart = stats.heart or stats.heart_count
    resp.id = user.id
    resp.nickname = user.nickname
    resp.private_account = user.private_account
    resp.verified = user.verified
    resp.video_count = stats.video_count
    resp.unique_id = username
    resp.signature = user.signature
    return resp


@rcache(ttl="4h", key="tiktok:recent:{username}")
async def fetch_latest_user_items(username: str):
    results: list[TikTokTopVideoItem] = []
    sigi: TikTokSigiStateRaw = await fetch_user_sigi_state(username)
    if not sigi:
        return None
    if not sigi.user_module or not sigi.item_module:
        return None
    author_model = await get_tiktok_profile(username)
    results.extend(
        TikTokTopVideoItem(
            title=item.desc,
            plays=item.stats.play_count,
            url=f"https://www.tiktok.com/@{username}/video/{awme_id}",
            comments=item.stats.comment_count,
            date=item.create_time,
            id=awme_id,
        )
        for awme_id, item in sigi.item_module.items()
    )
    for item in results[:5]:
        spawn_task(api_fetch_post(item.url), services.active_tasks)
    return TiktokTopUserVideoResults(
        count=len(results),
        items=sorted(results, key=lambda x: x.date, reverse=True),
        author=author_model,
    )


@router.get(
    "/{username}/recent",
    name="Get recent user TikToks",
    description="Fetch the inital (most recent) TikToks posted by the user",
    response_model=TiktokTopUserVideoResults,
)
async def fetch_recent_videos(request: Request, username: str):
    async with services.verify_token(request):
        async with asyncio.timeout(120):
            data: TiktokTopUserVideoResults = await fetch_latest_user_items(username)
        if not data:
            return UJSONResponse("Invalid username or invalid TikTok response data", status_code=404)
        return data


@router.get(
    "/{username}",
    description="Receive full metadata of a user's TikTok profile.",
    name="Get TikTok User",
)
async def fetch_tiktok_user(username: str, request: Request) -> TikTokUserProfileResponse:
    async with services.verify_token(request), asyncio.timeout(40):
        try:
            username = validate_tiktok_username(username)
        except ValueError:
            return UJSONResponse("Username did not pass validation requirements", 404)
        result = await get_tiktok_profile(username)
        return result or UJSONResponse("Invalid user requested", 404)


async def fetch_tiktok_top_videos(username: str, limit: int) -> list[TikTokTopVideoItem]:
    results: list[TikTokTopVideoItem] = []

    async with services.page_holder.borrow_page(proxy=True) as page:

        @log.catch(reraise=True)
        async def save_item_request(request: PlaywrightRequest):
            response = await request.response()
            if not response:
                return
            content = await response.header_value("content-type")
            if "item_list" in str(response.url) and content and "json" in content and (data := await response.body()):
                items = TikTokItemListRaw.parse_raw(data)

                log.info("Captured a response for {} items", len(items.item_list))
                for item in items.item_list:
                    with suppress(AttributeError):
                        item: ItemList
                        _item = TikTokTopVideoItem(
                            title=item.desc,
                            plays=item.stats.play_count,
                            url=f"https://www.tiktok.com/@{item.author.unique_id}/video/{item.id}",
                            comments=item.stats.comment_count,
                            date=item.create_time,
                            id=item.id,
                        )
                        results.append(_item)

        page.on("request", save_item_request)
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
            await page.locator('[data-e2e="liked-tab"]').dispatch_event("click")
            sigi: TikTokSigiStateRaw = await fetch_user_sigi_state(username)
            if sigi.item_module:
                for awme_id, item in sigi.item_module.items():
                    item: ItemModule
                    _item = TikTokTopVideoItem(
                        title=item.desc,
                        plays=item.stats.play_count,
                        url=f"https://www.tiktok.com/@{item.author_id}/video/{awme_id}",
                        comments=item.stats.comment_count,
                        date=item.create_time,
                        id=awme_id,
                    )
                results.append(_item)

            attempts = 0
            while True:
                await asyncio.sleep(2)
                pos = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                current_pos = await page.evaluate("document.body.scrollHeight")
                if current_pos == pos:
                    attempts += 1
                    if attempts > 7:
                        log.success("Scrolling assuming to be complete. Returning Now!")
                        break
                if len(results) > limit:
                    log.warning("Breaking on limit with {}", len(results))
                    break
                log.warning("Continuing to scroll to the bottom!")
            return unique(results, key=lambda x: x.url)
        finally:
            page.remove_listener("request", save_item_request)


@router.get(
    "/{username}/top",
    description="Fetch all TikToks posted by the user and sort them by play count.",
    name="Fetch User Top TikToks",
    response_model=TiktokTopUserVideoResults,
)
async def fetch_top_video(request: Request, username: str, limit: int = 500):
    async with services.verify_token(request):
        key = f"top_tiktok:{username}{limit}"
        async with services.locks[key]:
            cached = await services.redis.get(key)
            if not cached:
                results: list[TikTokTopVideoItem] = await fetch_tiktok_top_videos(username, limit)
                author: TikTokUserProfileResponse = await get_tiktok_profile(username)
                cached = TiktokTopUserVideoResults(count=len(results), author=author, items=results)
                if cached.items:
                    await services.redis.set(key, cached.json(), ex=900)

            else:
                cached = TiktokTopUserVideoResults.parse_raw(cached)
            return cached
