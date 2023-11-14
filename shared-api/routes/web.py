import asyncio
import os

import httpx
import orjson
import tekore as tk
import tldextract
import tuuid
import xxhash
import yarl
from fastapi.responses import RedirectResponse, Response, UJSONResponse
from melanie import BaseModel, CurlRequest, Field, alru_cache, capturetime, get_curl, get_redis, log, threaded, url_to_mime
from melanie.curl import get_curl
from melanie.doh import DnsQuery
from melanie.models.sharedapi.cashapp import CashAppProfile, CashappProfileResponse, CashappRaw
from melanie.models.sharedapi.discord_oauth import BaseModel, BasicMeInfo
from melanie.models.sharedapi.playlist import PlaylistResults, SoundcloudTrack, Track
from melanie.models.sharedapi.web import IPLookupResultResponse, IpScamScore, PuppeteerLoadMethod, ScreenshotResponse, TelegramProfileResponse, lookup_ip
from melanie.models.yt_dlp_search import YoutubeSearchResults, YTSearchRequest, do_search
from melanie.redis import rcache
from runtimeopt import DEBUG
from spotify.models import SpotifyStateHolder, SpotifyStateInfo, TekoreTokenDict, requesting_scopes
from starlette.responses import RedirectResponse, Response
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_406_NOT_ACCEPTABLE
from xxhash import xxh3_64_hexdigest

from api_services import api_username_var, media_url_from_request, services
from routes._base import APIRouter, Path, Query, Request, Response
from routes.ai import evaluate_image_url

from .discord_auth import exchange_code, request_get_redirect_uri

SEARCH_KEY: str = os.environ["SEARCH_KEY"]
yt_sem = asyncio.Semaphore(10)
router = APIRouter()

auth_states: dict[str, SpotifyStateHolder] = {}
sem = asyncio.BoundedSemaphore(3)


class RcloneFile(BaseModel):
    path: str = Field(..., alias="Path")
    name: str = Field(..., alias="Name")
    size: int | None = Field(..., alias="Size")
    mime_type: str = Field(..., alias="MimeType")
    mod_time: str = Field(..., alias="ModTime")
    is_dir: bool | None = Field(..., alias="IsDir")


class PlaylistLoadRequest(BaseModel):
    url: str


@rcache(ttl="2m")
async def fetch_telegram_user(username: str) -> TelegramProfileResponse | None:
    META_ATTRS = {"og:title": "name", "og:image": "avatar_url", "og:description": "description"}
    url = f"https://t.me/{username}"
    async with services.page_holder.borrow_page() as page:
        await page.goto(url, wait_until="domcontentloaded")
        resp = TelegramProfileResponse(username=username, url=url)
        await asyncio.sleep(0.12)

        async def get_attr_value(attr: str, target: str) -> str | None:
            s = await page.query_selector(f'meta[property="{attr}"]')
            value = await s.get_attribute("content")
            setattr(resp, target, value)

        try:
            await asyncio.gather(*[get_attr_value(attr, target) for attr, target in META_ATTRS.items()])
        except AttributeError as e:
            log.exception("Error collecting result {}", e)
            return None

        if resp.description:
            if resp.description.startswith("You can contact @"):
                resp.description = ""

            elif resp.description.startswith("You can view and join"):
                resp.description = ""

        return resp


@router.get("/api/web/telegram/{username}", name="Get Telegram USer", tags=["web"], response_model=TelegramProfileResponse)
async def fetch_tg_user(request: Request, username: str):
    async with services.verify_token(request), asyncio.timeout(30):
        return await fetch_telegram_user(username) or UJSONResponse("Invalid user", 404)


@alru_cache(ttl=90)
async def get_cashapp_user(username: str) -> CashappProfileResponse | None:
    async with asyncio.timeout(30), services.page_holder.borrow_page() as page:
        await page.goto(f"https://cash.app/${username}", wait_until="domcontentloaded")
        data = await page.evaluate("window.__NEXT_DATA__")
        data2 = await page.evaluate("profile")
        if not data:
            return None
        data.update(data2)
        data = orjson.dumps(data)
        _data = CashappRaw.parse_raw(data)
        profile = CashAppProfile(**orjson.loads(data))
        resp = CashappProfileResponse(profile=profile)
        for tag in _data.props.page_props.meta_tags:
            if tag.property == "og:image":
                resp.qr_image_url = tag.content

        return resp


def extract_hostname(url: str) -> str:
    extract = tldextract.extract(url)
    return f"{extract.domain}.{extract.suffix}"


async def is_porn_domain(domain: str) -> bool:
    redis = get_redis()
    async with services.locks["checkpurl"]:
        if await redis.scard("pornurls") < 100:
            with capturetime("loads porn urls"):
                curl = get_curl()
                r = await curl.fetch("https://raw.githubusercontent.com/Bon-Appetit/porn-domains/master/block.txt")
                domains = list(r.body.decode().splitlines())
                async with services.redis.pipeline() as pipe:
                    pipe.delete("pornurls")
                    pipe.sadd("pornurls", *domains)
                    pipe.expire("pornurls", 259200)
                    await pipe.execute()
    return bool(await redis.sismember("pornurls", domain))


@alru_cache
async def screenshot_page(url: str, full_page: bool = False, imgtype: str = "png", until: str = "load"):
    params = {"blockAds": "true", "stealth": "true", "proxy-server": "socks5://warp:1080"}

    json_data = {
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
        "url": url,
        "viewport": {
            "hasTouch": True,
            "height": 1920,
            "width": 1080,
        },
        "options": {
            "fullPage": full_page,
            "omitBackground": True,
            "quality": 85,
            "type": imgtype,
            "encoding": "binary",
        },
        "gotoOptions": {"timeout": 50000, "waitUntil": until},
    }

    uri = yarl.URL("https://chrome.melaniebot.net/screenshot")
    uri = uri.update_query(
        {
            "--proxy-server": "socks5://warp:1080",
        },
    )

    r = await services.htx.post(str(uri), params=params, json=json_data)
    try:
        r.raise_for_status()
    except httpx.HTTPError:
        log.exception("HTTP error {}", r.text)

    key = f"s{xxhash.xxh32_hexdigest(str(url))}_{tuuid.tuuid()}.{imgtype}"
    await services.save_target(key, bytes(r.content))
    return ScreenshotResponse(url=media_url_from_request(key))


@router.get("/api/web/screenshot", description="Screenshot a webpage with Chrome", name="Screenshot web", tags=["web"])
async def make_page_screenshot(
    request: Request,
    url: str = Query(..., description="URL to screnshot"),
    user_id: str = Query(default=None, description="User ID requesting the screenshot."),
    safe: bool = Query(default=True, description="Check the URL's domain against porn malware lists."),
    until: PuppeteerLoadMethod = Query(PuppeteerLoadMethod.load, description="puppeteer wait method."),
    nsfw_check: bool = Query(default=True, description="Run the image result through AI content moderation  "),
    full_page: bool = Query(default=False, description="Waits for page to load, scrolls to bottom"),
    image_type: str = Query(default="jpeg", description="png or jpeg"),
    imgerr: bool = Query(default=True, description="OK to return an image on failure or send HTTP error code"),
) -> Response:
    assert image_type in {"png", "jpeg", "jpg"}
    result = None
    curl = get_curl()
    async with services.verify_token(request):
        async with asyncio.timeout(30):
            if await services.redis.ratelimited(f"screenshot_request2:{user_id}", 4, 45):
                log.warning("High usage from user ID", user_id)
            async with services.locks[f"screenshot_page{url}{full_page}"]:
                host = extract_hostname(url)

                if nsfw_check:
                    url_mime = url_to_mime(url)
                    if url_mime and url_mime[0] and "image" in url_mime[0]:
                        check = await evaluate_image_url(url)
                        if (not check or not check.racy) and not check.adult:
                            return UJSONResponse(
                                f"Screenshot failed moderation screen. Detedted as {check.adult_classification_score} adult, {check.racy_classification_score} racy",
                                HTTP_400_BAD_REQUEST,
                            )

                        if imgerr:
                            result = ScreenshotResponse(url="https://emoji.melaniebot.net/clown.png")
                _r = await DnsQuery.resolve(host)
                if not _r.address:
                    return UJSONResponse(f"{host} can't even resolve", HTTP_400_BAD_REQUEST)
                if safe and await is_porn_domain(host):
                    if imgerr:
                        result = ScreenshotResponse(url="https://emoji.melaniebot.net/clown.png")
                    else:
                        return UJSONResponse(f"{host} is a registered porn domain", HTTP_400_BAD_REQUEST)
                if not result:
                    result = await screenshot_page(url, full_page, image_type, until)
                if not result:
                    return UJSONResponse("Unable to screenshot the page", status_code=HTTP_406_NOT_ACCEPTABLE)
                if nsfw_check:
                    check = await evaluate_image_url(result.url)
                    if check.racy or check.adult:
                        if imgerr:
                            result = ScreenshotResponse(url="https://emoji.melaniebot.net/clown.png")
                        else:
                            return UJSONResponse(
                                f"Screenshot failed moderation screen. Detedted as {check.adult_classification_score} adult, {check.racy_classification_score} racy",
                                HTTP_400_BAD_REQUEST,
                            )
                if result:
                    r = await curl.fetch(result.url)
                    return Response(r.body, media_type=f"image/{image_type}")
                return None


@router.post(
    "/api/youtube/search",
    description="Search Youtube for videos given a query",
    name="Search YouTube",
    tags=["youtube"],
    response_model=YoutubeSearchResults,
)
async def youtube_search_query(payload: YTSearchRequest, request: Request):
    async with services.verify_token(request, payload.query):
        query = payload.query
        query = " ".join(query.split()).lower()
        key = xxh3_64_hexdigest(query)
        async with services.locks[key], yt_sem, asyncio.timeout(10):
            cached = await services.redis.exhget("ytsearch_cache", key)
            if cached:
                data = YoutubeSearchResults.parse_raw(cached)
            else:
                if not query:
                    return YoutubeSearchResults(results=[])
                try:
                    data = await do_search(query)
                except TimeoutError:
                    return YoutubeSearchResults(results=[])
                data = YoutubeSearchResults.parse_raw(data)
                await services.redis.exhset("ytsearch_cache", key, data.json(exclude_none=True), ex=604800)
            for r in data.results:
                url = r.original_url or r.url
                r.original_url = url
            return data


@rcache(ttl="12h")
async def get_fraud_score(ip: str) -> IpScamScore:
    async with services.page_holder.borrow_page() as page:
        await page.goto(f"https://scamalytics.com/ip/{ip}", wait_until="domcontentloaded")
        s = await page.query_selector("body > div.container > div:nth-child(5) > pre")
        return IpScamScore.parse_raw(await s.text_content())


@router.get("/api/web/ip/{address}", description="Get information on an IP address", name="IP lookup", tags=["web"], response_model=IPLookupResultResponse)
async def api_ip_address(request: Request, address: str = Path(..., description="Full IP address or range to query info on")):
    async with services.verify_token(request):
        data = await lookup_ip(address)
        if data:
            result = IPLookupResultResponse.parse_raw(data)
            result.fraud_score = await get_fraud_score(result.ip)
            return result
        return None


@router.get("/api/cashapp/{username}", name="Get Cashapp profile", tags=["web"], description="Fetch a Cashapp user!", response_model=CashappProfileResponse)
async def api_cashapp_user(request: Request, username: str):
    async with services.verify_token(request, description=f"cahapp {username}"):
        profile = await get_cashapp_user(username)
        return profile or UJSONResponse("invalid cashapp user 🤨", 404)


def get_tk_userauth() -> tk.UserAuth:
    scope = tk.Scope(*requesting_scopes)
    return tk.UserAuth(services.sp_cred, scope)


async def get_basic_userinfo(code: str, redirect_uri: str) -> BasicMeInfo:
    curl = get_curl()
    token_data = await exchange_code(code, "melanie", redirect_uri)
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    r = await curl.fetch(CurlRequest("https://discord.com/api/v10/users/@me", headers=headers))
    if r.code > 300:
        raise ValueError(r.body)

    return BasicMeInfo.parse_raw(r.body)


@router.get("/spotify_exchange", include_in_schema=False)
async def spotify_exchange(request: Request, code: str, state: str):
    async with services.verify_token(request, public=True):
        redirect_uri = request_get_redirect_uri(request)
        userinfo = await get_basic_userinfo(code, redirect_uri)
        auth = get_tk_userauth()
        info = await SpotifyStateInfo.from_init_key(userinfo.id, state)
        if not info:
            return UJSONResponse("Invalid state. Use a URL intended for you.", 400)
        auth_states[auth.state] = SpotifyStateHolder(auth=auth, info=userinfo, init_state=state, rebound_url=info.rebound_url)
        return RedirectResponse(auth.url)


@router.get("/sp_callback", include_in_schema=False)
async def login_callback(code: str, state: str):
    holder = auth_states.pop(state, None)
    if not holder:
        return UJSONResponse("Invalid state. Use a URL intended for you.", 400)
    auth = holder.auth
    user_token: tk.Token = await auth.request_token(code, state)
    holder.token = TekoreTokenDict(
        access_token=user_token.access_token,
        refresh_token=user_token.refresh_token,
        expires_at=user_token.expires_at,
        scope=str(user_token.scope),
        uses_pkce=user_token.uses_pkce,
        token_type=user_token.token_type,
    )
    await holder.save_redis()
    if holder.rebound_url:
        return RedirectResponse("https://melaniebot.net/")
    return None


@threaded
def download_sc_urls(urls: str, full_dir_path: str) -> None:
    import shutil
    import tempfile
    from contextlib import chdir
    from pathlib import Path

    import filetype
    import msgpack
    import yt_dlp

    # from xxhash import
    from tair import Tair

    with Tair.from_url(os.environ["REDIS_URL"]) as redis, tempfile.TemporaryDirectory() as dir, chdir(dir), yt_dlp.YoutubeDL(
        {
            "allow_multiple_audio_streams": True,
            "continuedl": False,
            "external_downloader": {"default": "ffmpeg"},
            "extract_flat": "discard_in_playlist",
            "final_ext": "m4a",
            "format": "bestaudio",
            "ignoreerrors": "only_download",
            "fragment_retries": 2,
            "http_headers": {"authorization": " OAuth 2-294123-53425873-4zhArF3dYEHs208"},
            "nopart": True,
            "outtmpl": {"default": "%(title)s.%(ext)s"},
            "overwrites": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "nopostoverwrites": False, "preferredcodec": "m4a"},
                {"add_chapters": True, "add_infojson": "if_exists", "add_metadata": True, "key": "FFmpegMetadata"},
                {"key": "FFmpegConcat", "only_multi_video": True, "when": "playlist"},
            ],
            "retries": 0,
            "verbose": False,
        },
    ) as yt:
        for url in urls:
            if cached := redis.exhget("scdl_cache", url):
                data = msgpack.unpackb(cached)
                filename = data["filename"]
                payload = data["payload"]
                f = Path(filename)
                f.write_bytes(payload)

            else:
                info = yt.extract_info(url)
                _path = Path(info["requested_downloads"][0]["filepath"])
                if not _path.exists():
                    raise ValueError
                redis.exhset("scdl_cache", url, msgpack.packb({"payload": _path.read_bytes(), "filename": str(_path.name)}), ex=259200)

        for file in Path(dir).iterdir():
            mime = filetype.guess_mime(str(file))
            if mime and "audio" in mime:
                shutil.move(file, full_dir_path)


@threaded
def make_dir(path) -> None:
    from pathlib import Path

    f = Path(path)

    import shutil

    shutil.rmtree(str(path), ignore_errors=True)
    f.mkdir(parents=True)


@threaded
def clean_dir(name) -> None:
    import subprocess

    subprocess.check_output(["rclone", "--transfers", "9", "delete", f"cdrive:audio/{name}"], timeout=30, start_new_session=True)


@threaded
def finalize_download_dir(full_dir_path: str, upload_name: str, timeout: int = 90):
    import shutil
    import subprocess
    from pathlib import Path

    from loguru import logger as log
    from melanie import capturetime
    from sh import rclone

    new_upload_dir = Path(f"{full_dir_path}_final")
    shutil.rmtree(new_upload_dir.absolute(), ignore_errors=True)
    new_upload_dir.mkdir()
    log.info(new_upload_dir)

    for f in Path(full_dir_path).iterdir():
        log.info(f)
        new_name = f.name.replace(",", "").lower()
        new_path = new_upload_dir / new_name
        shutil.copy(str(f.absolute()), new_path)

    with capturetime("Detox"):
        subprocess.check_output(["/usr/bin/detox", "--remove-trailing", new_upload_dir], start_new_session=True)

    assert new_upload_dir.exists()

    with capturetime("transfer"):
        rclone.copy(str(new_upload_dir.absolute()), f"cdrive:audio/{upload_name}", P=True, transfers=32, verbose=True)
    with capturetime("listing"):
        _data = str(rclone.lsjson(f"cdrive:audio/{upload_name}"))
    return orjson.loads(_data)


async def do_search_sp(track: Track) -> None:
    with capturetime(f"{track.search_str}totals"):
        async with services.track_sem:

            @threaded
            def search() -> None:
                result = next(services.soundcloud.search_tracks(track.search_str), None)
                if result:
                    track.soundcloud_track = SoundcloudTrack(**dict(result.__dict__))

            key = f"sc2:{track.search_str}"
            cached = await services.redis.get(key)
            if cached:
                track.soundcloud_track = SoundcloudTrack.parse_raw(cached)
            else:
                await search()
                if track.soundcloud_track:
                    await services.redis.set(key, track.soundcloud_track.json(), ex=300)


@router.get("/api/spotify/playlist/{playlist_id}", name="Load a Spotify playlist", tags=["web"])
async def load_playlist(
    request: Request,
    playlist_id: str,
    plain: bool = False,
    transform: bool = False,
    name: str | None = None,
    clean: bool = False,
):
    async with services.verify_token(request):
        if not DEBUG and api_username_var.get() not in ("m@monteledwards.com", "Melanie"):
            return UJSONResponse("Not an authorized API user for this route", 403)
        async with asyncio.timeout(120):
            while not services.sp:
                await asyncio.sleep(0.1)
            async with services.locks[playlist_id]:
                tracks = await services.sp.playlist_items(playlist_id=playlist_id, limit=None, as_tracks=True)
                data = PlaylistResults.parse_obj(tracks)
                if transform:
                    async with asyncio.TaskGroup() as tg:
                        for item in data.items:
                            if item.track:
                                tg.create_task(do_search_sp(item.track))

                    for item in data.items:
                        if item.track and item.track.soundcloud_track:
                            data.soundcloud_urls.append(item.track.soundcloud_track.permalink_url)
                if not name:
                    return UJSONResponse("Name missing", 401)
                if clean:
                    await clean_dir(name)
                dirname = f"/cache/audio/{xxhash.xxh32_hexdigest(playlist_id)}".lower()
                await make_dir(dirname)
                await download_sc_urls(data.soundcloud_urls, dirname)
                results = await finalize_download_dir(dirname, name)
                tracks = [RcloneFile(**i) for i in results]
                for t in tracks:
                    data.urls.append(f"https://audio.hurt.af/{name}/{t.path}")
                data.items = None
                if plain:
                    output = "\n".join(data.soundcloud_urls)
                    return Response(output, media_type="text/plain")
                return data
