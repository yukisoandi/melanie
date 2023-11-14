import asyncio
import asyncio.staggered
import io
import random
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from typing import ParamSpec, TypeVar

import msgpack
import orjson
import regex as re
import stackprinter
import yarl
from boltons.urlutils import find_all_links
from discord_webhook.async_webhook import AsyncDiscordWebhook
from discord_webhook.webhook import DiscordEmbed
from fastapi.responses import UJSONResponse
from melanie import capturetime, checkpoint, log, url_to_mime
from melanie.helpers import extract_json_tag
from melanie.models.sharedapi.instagram import (
    HighlightItem,
    InstagramCarouselMediaResponse,
    InstagramHighlightGraphQueryRaw,
    InstagramHighlightIndexResponse,
    InstagramHighlightMediaItem,
    InstagramHighlightRaw,
    InstagramHighlightResponse,
    InstagramPostItem,
    InstagramPostRequest,
    InstagramPostResponse,
    InstagramProfileModel,
    InstagramProfileModelResponse,
    InstagramStoryResponse,
    InstagramUserResponse,
    InstaStoryModel,
    StoryItem,
    UserPostItem,
)
from melanie.models.sharedapi.instagram.embed import InstagramEmbedDataRaw
from melanie.models.sharedapi.instagram.instagram_post2 import Caption, InstagramCarouselMediaResponse, InstagramPostItem, InstagramUserResponse
from melanie.models.sharedapi.instagram.reels_index import IgReelsIndexRaw
from melanie.models.sharedapi.threads import ThreadsDataRaw
from melanie.redis import get_redis, rcache
from melanie.timing import capturetime
from playwright._impl._api_types import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import Request as PlaywrightRequest
from playwright.async_api import Response as PlaywrightResponse
from starlette.status import HTTP_404_NOT_FOUND, HTTP_429_TOO_MANY_REQUESTS
from tornado import ioloop

from api_services import api_username_var, request_id_var, services
from core import PlaywrightError, media_url_from_request
from routes._base import APIRouter, Query, Request

IG_RE = re.compile(
    "(?:https?:\\/\\/)?(?:www.)?instagram.com\\/?([a-zA-Z0-9\\.\\_\\-]+)?\\/(;+)?([reel]+)?([tv]+)?([stories]+)?\\/([a-zA-Z0-9\\-\\_\\.]+)\\/?([0-9]+)?",
)
REPLACE_SHARE_RE = re.compile("\\?igshid.*")
REPLACE_SHARE_RE2 = re.compile("\\?utm_source.*")
REPLACE_SHARE_RE3 = re.compile("\\?img_index.*")
P = ParamSpec("P")
T = TypeVar("T")
router = APIRouter(tags=["instagram"], prefix="/api/instagram")
redis = get_redis()
ig_limiters = defaultdict(partial(asyncio.Semaphore, 2))


def later(f: Callable[P, T]) -> None:
    def _background(*a, **ka):
        loop = ioloop.IOLoop.current()
        if ka:
            method = partial(f, *a, **ka)

            loop.add_callback(method)
        else:
            loop.add_callback(f, *a)

    return _background


@later
async def report_invalid_request(target, user_id=None, error: str | None = None, guild_id=None, attempted_ctx=None, extra=None):
    hook = AsyncDiscordWebhook(
        url="https://discord.com/api/webhooks/1154163322623643689/JSD9yr2W3DEcn79bPtlYlOeP0bQfm-4Et7uki6azFAytSZcKCjYZ7JIStty1PlFty2V9",
    )

    lookup_query = """
        select last(user_name, created_at)   username,
        last(guild_name, created_at)  gname,
        last(user_avatar, created_at) avatar,
        last(channel_name, created_at) channel

        from guild_messages
            where guild_id = $1
            and user_id = $2

            """

    embed = DiscordEmbed(title="Instagram post failure")
    embed.description = f"target: {target}"

    if error:
        e_len = len(error)
        if e_len > 1900:
            hook.add_file(error.encode("UTF-8"), "error.py")
        else:
            hook.set_content(f"<@728095627757486081>\n```py\n{error}\n```")
    res = await services.pool.fetchrow(lookup_query, str(guild_id), str(user_id))
    if res:
        embed.add_embed_field(name="Guild", value=f'{res["gname"]}\n{guild_id}')
        embed.add_embed_field(name="User", value=f"{res['username']}\n{user_id}")
        embed.add_embed_field(name="Channel", value=str(res["channel"]))
        embed.set_thumbnail(url=f"https://cdn.discordapp.com/avatars/{user_id}/{res['avatar']}.webp?size=1024")

    else:
        embed.add_embed_field(name="Guild", value=str(guild_id))
        embed.add_embed_field(name="User", value=str(user_id), inline=True)
    embed.add_embed_field(name="API User", value=str(api_username_var.get()), inline=True)
    if attempted_ctx:
        embed.add_embed_field(name="Attempted Context", value=str(attempted_ctx), inline=True)
    embed.add_embed_field(name="Request ID", value=str(request_id_var.get()), inline=True)
    if extra:
        embed.add_embed_field(name="Extra", value=str(extra), inline=True)

    hook.add_embed(embed)
    await hook.execute()
    log.success("Failure for {} reported OK", target)


def ttl_20_30(*a, **ka) -> int:
    return random.randint(1800, 2700)


def is_valid_url(url: str) -> bool:
    _url = yarl.URL(str(url))
    return all([_url.scheme, _url.host, _url])


def validate_instagram_username(name: str) -> str:
    name = name.replace("'b", "")
    name = name.replace("'", "")
    name = name.lower()
    if len(name) > 30:
        msg = "Usernames must be less than 30 characters"
        raise ValueError(msg)
    name = str(name).removeprefix("@")
    allowed_chars = ("_", ".")
    if name.endswith("."):
        msg = "Usernames cannot end with periods"
        raise ValueError(msg)
    for c in name:
        if not c.isalnum() and c not in allowed_chars:
            msg = f"{c} is not allowed in Instagram usernames"
            raise ValueError(msg)
    return name


def remove_share_id(url: str) -> str:
    url = REPLACE_SHARE_RE.sub("", str(url))
    url = REPLACE_SHARE_RE2.sub("", url)
    url = REPLACE_SHARE_RE3.sub("", url)

    url = url.replace("https://www.instagram.com/reels/", "https://www.instagram.com/p/")
    url = url.replace("https://www.instagram.com/reel/", "https://www.instagram.com/p/")
    return url.removesuffix("/")


async def check_ig_ctx():
    invalid = []
    valid = []
    final = {"invalid": invalid, "valid": valid}
    for username, ctx in services.page_holder.browser_contexts.items():
        page = await ctx.new_page()
        try:
            await page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
            data = await page.content()
            if username in data and "Edit profile" in data:
                log.error("Context {} is likely invalid", username)
                invalid.append(username)
            else:
                log.success("Context {} is authenticated OK", username)
                valid.append(username)
            await asyncio.sleep(1.2)
        finally:
            await page.close()
    return final


async def iglogin(user: Page | str, proxy=True) -> bool:
    user = user.username if isinstance(user, Page) else user
    login = None

    if login == "proxy":
        return
    if not user:
        msg = "Page has no username"
        raise ValueError(msg)
    for k, v in services.instagram_credentials.items():
        if k == user:
            login = v

            break

        if v.alias == user:
            login = v
            break
    if not login:
        if user == "proxy":
            return
        msg = f"No saved credential for user {user}"
        raise ValueError(msg)

    lock = services.locks[f"relogin:{user}"]

    try:
        async with asyncio.timeout(0.00001):
            await lock.acquire()

    except TimeoutError:
        return

    else:
        try:
            if user not in services.page_holder.browser_contexts:
                browser = services.page_holder.proxy_browser if proxy else services.page_holder.browser
                services.page_holder.browser_contexts[user] = await browser.new_context(**services.page_holder.default_args)
            ctx = services.page_holder.browser_contexts[user]
            page = await ctx.new_page()
            page.username = user
            await page.goto("https://www.instagram.com/")
            username_loc = page.get_by_label("username")
            await username_loc.fill(login.email)
            await asyncio.sleep(random.uniform(0.5, 1))
            password_loc = page.get_by_label("Password")
            await password_loc.fill(login.password)
            login_button = page.get_by_role("button", name="Log in", exact=True)
            await login_button.click()
            await asyncio.sleep(5)

            if "login" not in page.url:
                log.success("Login success!!! {}", user)

            await asyncio.sleep(15)
            await page.goto("https://yahoo.com")
            state = await page.context.storage_state()
            msgpack.packb(state)

            with capturetime(f"save {user}"):
                await services.redis.hset("api_sessions_store., user, payload")

        finally:
            lock.release()


async def resolve_redirect_target(url: str) -> tuple[str, str | None]:
    _url = yarl.URL(url)
    target = None
    media_id = _url.query.get("story_media_id", None)
    if "instagram.com/p/" in url or "reel" in url or "instagram.com/reel" in url:
        target = url
    if not target:
        async with services.page_holder.borrow_page() as page:
            await page.goto(url, wait_until="commit")
            target = str(page.url)
    return target, media_id


async def process_story_response(story: InstagramStoryResponse) -> None:
    for item in story.items:
        item.url = media_url_from_request(item.filename)
        item.id = item.id.split("_")[0]
    if story.author:
        story.author.avatar_url = media_url_from_request(story.author.avatar_filename)


async def resolve_highlight_url(url, _media_id, api_user=None):
    _highlight_id = str(url).split("/highlights/")[-1]
    log.warning("Fetching highlight item {}", _highlight_id)
    data2: InstagramHighlightRaw = await fetch_ig_highlight_id(_highlight_id, api_user)
    final = InstagramPostResponse(num_results=1, share_url=url)
    hi = data2.reels_media[0]
    final.author = InstagramUserResponse(username=hi.user.username, full_name=hi.user.full_name, is_private=hi.user.is_private, is_verified=hi.user.is_verified)
    filename, _task = services.insta.start_render(hi.user.profile_pic_url, "Instagram")
    final.author.avatar_filename = filename
    final.author.avatar_url = media_url_from_request(filename)
    for pi in hi.items:
        p = InstagramPostItem(id=pi.id, caption=pi.caption, taken_at=pi.taken_at, title=f"Highlight by @{final.author.username}")
        if pi.video_versions:
            p.is_video = True
            target_url = pi.video_versions[0].url
            filename, _task = services.insta.start_render(target_url, "Instagram", passive=True)
            p.video_filename = filename
        else:
            p.is_video = False
            for choice in pi.image_versions2.candidates:
                _mime = url_to_mime(choice.url)
                if _mime and "heic" not in _mime[0]:
                    target_url = choice.url
                    filename, _task = services.insta.start_render(target_url, "Instagram", passive=True)
                    p.image_filename = filename
                    break
        _total = len(pi.image_versions2.candidates)
        _middle = _total // 2
        middle = pi.image_versions2.candidates[_middle]
        pi.image_versions2.candidates.remove(middle)
        pi.image_versions2.candidates.insert(0, middle)
        for choice in pi.image_versions2.candidates:
            _mime = url_to_mime(choice.url)
            if _mime and "heic" not in _mime[0]:
                target_url = choice.url
                filename, _task = services.insta.start_render(target_url, "Instagram", passive=True)
                p.preview_image_filename = filename
                break
        final.items.append(p)

    if _media_id:
        _search = filter(lambda x: str(x.id).split("_")[0] == _media_id, final.items)
        if target_item := next(_search, None):
            log.success("Found highlight media id. Moving this item to the front of the result..")
            final.items.remove(target_item)
            final.items.insert(0, target_item)

    return final


async def resolve_threads_post(url: str) -> ThreadsDataRaw:
    async with asyncio.timeout(15):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        async def find_graphql(r: PlaywrightRequest):
            if fut.done():
                return

            if r.url == "https://www.threads.net/api/graphql":
                resp = await r.response()

                body = await resp.body()

                fut.set_result(body)

        async with services.page_holder.borrow_page() as page:
            page.on("request", find_graphql)

            try:
                await page.goto(url)
                data: bytes = await fut

            finally:
                page.remove_listener("request", find_graphql)

        return ThreadsDataRaw.parse_raw(data)


async def process_threads_response(data: ThreadsDataRaw) -> InstagramPostResponse:
    final = InstagramPostResponse()
    final.author = InstagramUserResponse()
    ti = data.data.data.containing_thread.thread_items[0].post
    filename, task = services.insta.start_render(ti.user.profile_pic_url, "Threads")

    final.author.avatar_filename = filename
    final.author.avatar_url = media_url_from_request(filename)
    final.author.is_verified = ti.user.is_verified
    final.author.username = ti.user.username

    for item in data.data.data.containing_thread.thread_items:
        ti = item.post
        pi = InstagramPostItem(like_count=ti.like_count)

        if item.view_replies_cta_string:
            pi.reply_count = int(item.view_replies_cta_string.split(" ")[0])

        if ti.image_versions2 and ti.image_versions2.candidates:
            filename, task = services.insta.start_render(ti.image_versions2.candidates[0].url, "Threads")

            pi.image_filename = filename
            pi.image_url = media_url_from_request(filename)
            total = len(ti.image_versions2.candidates)
            middle = total // 2
            filename, task = services.insta.start_render(ti.image_versions2.candidates[middle].url, "Threads")

            pi.preview_image_filename = filename
            pi.preview_image_url = media_url_from_request(filename)

        if ti.video_versions:
            filename, task = services.insta.start_render(ti.video_versions[0].url, "Threads")

            pi.video_filename = filename

            pi.video_url = media_url_from_request(filename)

        if ti.caption:
            pi.caption = Caption(text=ti.caption.text)

        pi.taken_at = ti.taken_at

        pi.id = ti.id

        if ti.carousel_media:
            for ci in ti.carousel_media:
                cm = InstagramCarouselMediaResponse()
                if ci.video_versions:
                    cm.is_video = True
                    filename, task = services.insta.start_render(ci.video_versions[0].url, "Threads")
                    if filename == pi.image_filename:
                        continue
                    cm.filename = filename
                    cm.url = media_url_from_request(filename)
                    if ci.image_versions2 and ci.image_versions2.candidates:
                        total = len(ci.image_versions2.candidates)
                        middle = total // 2
                        filename, task = services.insta.start_render(ci.image_versions2.candidates[middle].url, "Threads")

                        cm.preview_image_filename = filename
                        cm.preview_image_url = media_url_from_request(filename)

                else:
                    cm.is_video = False
                    filename, task = services.insta.start_render(ci.image_versions2.candidates[0].url, "Threads")
                    if filename == pi.image_filename:
                        continue
                    cm.filename = filename
                    cm.url = media_url_from_request(filename)
                pi.sidecars.append(cm)
            pi.sidecar_count = len(pi.sidecars)

        final.items.append(pi)

    final.share_url = f"https://www.threads.net/t/{ti.code}"

    final.num_results = len(final.items)

    return final


async def try_embed_fetch(url: str) -> InstagramEmbedDataRaw | None:
    async with services.page_holder.borrow_page(proxy=True) as page:
        url = url.removesuffix("/")
        url2 = f"{url}/embed/captioned"
        try:
            async with asyncio.timeout(10):
                try:
                    await page.goto(url2, wait_until="commit")
                    await page.bring_to_front()
                    await page.locator("body").click()
                    await asyncio.sleep(random.uniform(0.1, 0.17))
                    await page.mouse.wheel(0, random.uniform(500, 800))
                    await asyncio.sleep(random.uniform(0.1, 0.17))
                    data = await page.evaluate("window.__additionalData")
                    data = orjson.loads(orjson.dumps(data))
                    try:
                        if data and data["extra"] and data["extra"]["data"]:
                            log.success("Post {} resolved via embed path", url)
                            return InstagramEmbedDataRaw.parse_obj(data)
                    except KeyError:
                        return None
                except PlaywrightError:
                    return None
        except TimeoutError:
            log.warning("Timeout for embed fetch")
            return None


async def resolve_embed_data(data: InstagramEmbedDataRaw) -> InstagramPostResponse:
    final = InstagramPostResponse()
    d = data.extra.data.shortcode_media
    caption = None
    if d.edge_media_to_caption and d.edge_media_to_caption.edges and (d.edge_media_to_caption.edges[0] and d.edge_media_to_caption.edges[0].node):
        caption = Caption(text=d.edge_media_to_caption.edges[0].node.text)
    final.author = author = InstagramUserResponse()
    filename, task = services.insta.start_render(d.owner.profile_pic_url, "Instagram", suffix=".jpg")
    author.avatar_filename = filename
    author.avatar_url = media_url_from_request(filename)
    author.username = d.owner.username
    author.is_private = d.owner.is_private
    author.is_verified = d.owner.is_verified
    item = InstagramPostItem()
    item.id = d.id
    item.is_video = d.is_video
    item.title = d.title
    item.like_count = d.edge_liked_by.count
    item.comment_count = d.edge_media_to_comment.count
    item.caption = caption
    item.taken_at = d.taken_at_timestamp
    if d.is_video and d.video_url:
        item.is_video = True
        item.video_filename, task = services.insta.start_render(d.video_url, "Instagram", suffix=".mp4")

    elif d.display_url:
        item.image_filename, _task = services.insta.start_render(d.display_url, "Instagram", suffix=".jpg")

    if d.display_resources:
        item.preview_image_filename, _task = services.insta.start_render(d.display_resources[0].src, "Instagram", suffix=".jpg", passive=True)

    item.preview_image_url = media_url_from_request(filename)
    item.view_count = d.video_view_count

    if d.edge_sidecar_to_children and d.edge_sidecar_to_children.edges:
        for edge in d.edge_sidecar_to_children.edges:
            if edge.node:
                cm = InstagramCarouselMediaResponse()
                if edge.node.video_url:
                    cm.filename, _task = services.insta.start_render(edge.node.video_url, "Instagram", suffix=".mp4")
                    cm.is_video = True
                elif edge.node.display_url:
                    cm.filename, _task = services.insta.start_render(edge.node.display_url, "Instagram", suffix=".jpg")
                    cm.is_video = False
                if edge.node.display_resources:
                    cm.preview_image_filename, _task = services.insta.start_render(edge.node.display_resources[0].src, "Instagram", suffix=".jpg", passive=True)
                item.sidecars.append(cm)
        item.sidecar_count = len(item.sidecars)
    final.items.append(item)
    final.share_url = f"https://www.instagram.com/p/{d.shortcode}/"
    final.num_results = len(final.items)
    return final


async def _download_reels_index():
    async with services.page_holder.borrow_page() as page:
        r = await page.goto("https://www.instagram.com/reels/", wait_until="domcontentloaded")

        _data = await r.body()
        data = await extract_json_tag(_data, "xdt_api__v1__clips__home__connection")
        return IgReelsIndexRaw.parse_raw(data)


# https://www.instagram.com/s/aGlnaGxpZ2h0OjE4MzAwMzk0OTU3MDU5NzM0?igshid=MzRlODBiNWFlZA==


@rcache(ttl="12d", key="instagram_post:{url}")
async def do_api_post(url):
    final = None
    cached = False
    if "threads" in url:
        data = await resolve_threads_post(url)
        final = await process_threads_response(data)
        return None
    elif "instagram" in url:
        if "https://www.instagram.com/s/" in url:
            url, _media_id = await resolve_redirect_target(url)
            if "/stories/highlights" in url:
                log.info("Highlight link detected. Resolved:  {} {}", url, _media_id)
                final = await resolve_highlight_url(url, _media_id)

        if not final and "stories/highlights" not in url:
            embed_data = await try_embed_fetch(url)
            if embed_data:
                final = await resolve_embed_data(embed_data)
        if not final:
            final: InstagramPostResponse = await services.insta.get_insta_post(url)
            if not final or not final.items:
                return None
        if final and not cached:
            final.share_url = url
        return final
    return None


@router.post(
    "/post",
    name="Download Instagram Post",
    description="Fetch an Instagram post, story, reel or other media type.",
    response_model=InstagramPostResponse,
)
async def fetch_instagram_post(post_request: InstagramPostRequest, request: Request):
    links = find_all_links(post_request.content)
    target_link = next(
        (remove_share_id(str(l)) for l in links if "instagram" in str(l) or "threads" in str(l)),
        None,
    )
    if not target_link:
        return UJSONResponse("No link in this request?", 404)
    log.info(target_link)
    key = f"instagram_post:{target_link}"
    async with services.verify_token(request), services.locks[key]:
        if not await rcache.exists(key) and (api_username_var.get() == "Bleed" and await services.redis.get("disable_bleed_ig")):
            return UJSONResponse("Too many requests", 429)
        try:
            async with asyncio.timeout(25):
                final = await do_api_post(target_link)
                if not final:
                    msg = "No data returned from API fetch"
                    raise ValueError(msg)

                else:
                    for item in final.items:
                        if item.id:
                            item.id = item.id.split("_")[0]
                        if item.image_filename:
                            item.image_url = media_url_from_request(item.image_filename)
                        if item.video_filename:
                            item.video_url = media_url_from_request(item.video_filename)
                        if item.preview_image_filename:
                            item.preview_image_url = media_url_from_request(item.preview_image_filename)
                        if final.author and final.author.avatar_filename:
                            final.author.avatar_url = media_url_from_request(final.author.avatar_filename)
                        await checkpoint()
                        for cm in item.sidecars:
                            if cm.filename:
                                cm.url = media_url_from_request(cm.filename)
                            if cm.preview_image_filename:
                                cm.preview_image_url = media_url_from_request(cm.preview_image_filename)
                            await checkpoint()
                        item.sidecar_count = len(item.sidecars)

                    return final
        except asyncio.CancelledError:
            raise
        except Exception:
            if await services.redis.hget("insta_invalid_alerts", key) and (api_username_var.get() == "Bleed" and await services.redis.get("disable_bleed_ig")):
                try:
                    buf = io.StringIO()
                    stackprinter.show(file=buf)
                    report_invalid_request(
                        post_request.content,
                        error=buf.getvalue(),
                        user_id=post_request.user_id,
                        guild_id=post_request.guild_id,
                        attempted_ctx=None,
                    )
                finally:
                    await services.redis.hset("insta_invalid_alerts", key, 1)
                await rcache.set(key, None, expire="24h")
                log.warning("Returning 404 for IG post {}", post_request.content)
            return UJSONResponse("Post not found", HTTP_404_NOT_FOUND)


@rcache(ttl="2h", key="instahl:{username}")
async def fetch_ig_highlights(username: str, api_user: str | None = None) -> InstagramHighlightGraphQueryRaw:
    fut = asyncio.get_running_loop().create_future()

    async def handle_ql(r: PlaywrightRequest):
        if fut.done():
            return

        if "https://www.instagram.com/graphql/query/?query_hash" in r.url:
            resp = await r.response()
            body = await resp.body()

            if not body or "edge_highlight_reels" not in body.decode("UTF-8"):
                return
            if fut.done():
                return
            with suppress(asyncio.InvalidStateError):
                fut.set_result(body)

                log.success("Highlights found in XMR request {} ", r.url)

    async with services.page_holder.borrow_page(user=api_user) as page:
        page.on("request", handle_ql)
        try:
            await page.goto(f"https://www.instagram.com/{username}", wait_until="domcontentloaded")
            html = await page.content()
            _data = await extract_json_tag(html, "edge_highlight_reels")
            if _data:
                log.success("Highlights found in HTML body {} ")
                return InstagramHighlightGraphQueryRaw.parse_obj(_data)
            else:
                data = await fut

                return InstagramHighlightGraphQueryRaw.parse_raw(data)

        finally:
            fut.cancel()
            page.remove_listener("request", handle_ql)


@rcache(ttl="4h", key="instahl:{highlight_id}")
async def fetch_ig_highlight_id(highlight_id: str, api_user: str) -> InstagramHighlightRaw:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    async def handle_req(r: PlaywrightResponse):
        if fut.done():
            return

        with suppress(PlaywrightError):
            if r.request.resource_type in ("xhr", "script"):
                if "feed/reels_media" in r.url:
                    data = await r.body()
                    data = orjson.loads(data)
                    fut.set_result(orjson.dumps(data))
                elif "api/graphql" in r.url:
                    data = await r.body()
                    data = orjson.loads(data)
                    con = data["data"].get("xdt_api__v1__feed__reels_media__connection")
                    if con:
                        reels_media = []
                        edges = con["edges"]
                        for e in edges:
                            node = e["node"]
                            reels_media.append(node)
                        fut.set_result(orjson.dumps({"reels_media": reels_media, "status": "ok"}))

    async with services.page_holder.borrow_page(user=api_user) as page:
        page.on("response", handle_req)
        data = None
        try:
            await page.goto(f"https://www.instagram.com/stories/highlights/{highlight_id}", wait_until="domcontentloaded")
            html = await page.content()
            _data = await extract_json_tag(html, "xdt_api__v1__feed__reels_media__connection")
            if _data:
                _data = orjson.loads(_data)
                con = _data.get("xdt_api__v1__feed__reels_media__connection")
                if con:
                    reels_media = []
                    edges = con["edges"]
                    for e in edges:
                        node = e["node"]
                        reels_media.append(node)
                    data = orjson.dumps({"reels_media": reels_media, "status": "ok"})

            if not data:
                data = await fut
            if data:
                return InstagramHighlightRaw.parse_raw(data)
        finally:
            if not fut.done():
                fut.cancel()
            page.remove_listener("response", handle_req)


@rcache(ttl=ttl_20_30, key="instastory:{username}")
async def get_user_story(username: str, ctx: str | None = None) -> InstagramStoryResponse:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    data = None
    async with services.page_holder.borrow_page(user=ctx) as page:

        async def on_request(r: PlaywrightRequest):
            if "https://www.instagram.com/api/v1/feed/reels_media/" in r.url:
                resp = await r.response()
                payload = await resp.body()
                with suppress(asyncio.InvalidStateError):
                    future.set_result(payload)

        page.on("request", on_request)
        try:
            await page.goto(f"https://www.instagram.com/stories/{username}/", wait_until="domcontentloaded")
            if username not in page.url:
                return None
            if f"https://www.instagram.com/{username}/" in page.url:
                return None
            with suppress(PlaywrightError):
                if "Page not found" in await page.title():
                    log.warning("Username {} is likely invalid.", username)
                    return None

            html = await page.content()

            story_feed = await extract_json_tag(html, "xdt_api__v1__feed__reels_media")
            if story_feed:
                story_feed = orjson.loads(story_feed)
                data = orjson.dumps(story_feed["xdt_api__v1__feed__reels_media"])

        except PlaywrightError as e:
            log.error("Unknown playwright erorr of type {}", e)
            return None

        except TimeoutError:
            log.exception("Timeout on stories")
            return None
        finally:
            page.remove_listener("request", on_request)

    if not data:
        return

    data = InstaStoryModel.parse_raw(data)
    final = InstagramStoryResponse()

    if not data.reels_media:
        return None
    s2 = data.reels_media[0].user
    final.author = InstagramUserResponse(username=s2.username, full_name=s2.full_name, is_private=s2.is_private, is_verified=s2.is_verified)
    if s2.profile_pic_url:
        final.author.avatar_filename, task = services.insta.start_render(s2.profile_pic_url, "Instagram", suffix=".jpg")
    for reel in data.reels_media:
        for item in reel.items:
            story = StoryItem()
            final.items.append(story)
            story.taken_at = item.taken_at
            story.id = item.id
            if item.video_versions:
                target_url = item.video_versions[0].url
                story.is_video = True
                story.filename, task = services.insta.start_render(target_url, "Instagram", suffix=".mp4", passive=True)
            else:
                for choice in item.image_versions2.candidates:
                    _mime = url_to_mime(choice.url)
                    if _mime and "heic" not in _mime[0]:
                        target_url = choice.url
                        story.filename, task = services.insta.start_render(target_url, "Instagram", suffix=".jpg", passive=True)
                        break
                story.is_video = False
    final.item_count = len(final.items)
    final.created_at = time.time()
    return final


@router.get(
    "/story/{username}",
    name="Fetch Stories",
    description="Fetch a users current Instagram story. Does not mark a view on the instagram user.",
    operation_id="getInstaStories",
    response_model=InstagramStoryResponse,
)
async def fetch_ig_stories(
    request: Request,
    username: str,
    force: bool = Query(default=False, description="Bypass cache and fetch the latest user stories"),
    ctx: str | None = None,
):
    async with services.verify_token(request):
        try:
            username = validate_instagram_username(username)
        except ValueError as e:
            return UJSONResponse(e, HTTP_404_NOT_FOUND)
        key = f"instastory:{username}"
        api_user = api_username_var.get()
        async with services.locks[key], ig_limiters[f"insta_user:{api_user}"]:
            if force:
                await rcache.delete(key)

            if api_user == "Bleed" and await services.redis.get("disable_bleed_ig"):
                return UJSONResponse("Concurrency limit reached", HTTP_429_TOO_MANY_REQUESTS)
            try:
                async with asyncio.timeout(20):
                    data: InstagramStoryResponse = await get_user_story(username, ctx)

            except TimeoutError:
                log.error("Timeout fetching stories")

                await rcache.set(key="instastory:{username}", value=None, expire=3600)
                data = None

            if data and data.items:
                await process_story_response(data)
            else:
                return UJSONResponse("No stories found for that user", HTTP_404_NOT_FOUND)
            return data


@router.get(
    "/highlights/{username}",
    name="Get Highlights",
    description="Fetch a users Instagram highlights",
    operation_id="getInstaHighlights",
    response_model=InstagramHighlightIndexResponse,
)
async def get_user_highlights(request: Request, username: str, ctx: str | None = None):
    async with services.verify_token(request), services.locks[f"userhighlight:{username}"]:
        try:
            username = validate_instagram_username(username)
        except ValueError as e:
            return UJSONResponse(e, HTTP_404_NOT_FOUND)

        key = f"instahlindex:{username}"
        cached = await redis.get(key)
        if cached:
            cached = orjson.loads(cached)
            if cached is None:
                return UJSONResponse("No highlights", HTTP_404_NOT_FOUND)
            index = InstagramHighlightGraphQueryRaw.parse_obj(cached)

        else:
            try:
                async with asyncio.timeout(12):
                    index = await fetch_ig_highlights(username, api_user=ctx)
            except TimeoutError:
                index = None
            if index is None:
                await redis.set(key, orjson.dumps(None), ex=ttl_20_30())
                return UJSONResponse("No highlights", HTTP_404_NOT_FOUND)
            else:
                await redis.set(key, index.json(), ex=ttl_20_30())
        resp = InstagramHighlightIndexResponse()
        for item in index.data.user.edge_highlight_reels.edges:
            preview_url = item.node.cover_media.thumbnail_src
            filename, task = services.insta.start_render(preview_url, "Instagram", suffix=".jpg")
            highlight = HighlightItem(preview_img=media_url_from_request(filename), title=item.node.title, id=item.node.id)
            resp.highlights.append(highlight)
        resp.count = len(resp.highlights)
        return resp


@router.get(
    "/highlight/{highlight_id}",
    name="Get Highlight by ID",
    description="Load the media of a highlight",
    operation_id="getInstaHighlightId",
    response_model=InstagramHighlightResponse,
)
async def get_ig_highlight(request: Request, highlight_id: str, force: bool = False, ctx: str | None = None):
    async with services.verify_token(request):
        key = f"insta_highlightid:{highlight_id}"
        async with services.locks[key]:
            if force:
                await services.redis.delete(key)
            data = await services.redis.get(key)

            if data:
                data = InstagramHighlightRaw.from_bytes(data)
            else:
                async with asyncio.timeout(30):
                    data: InstagramHighlightRaw = await fetch_ig_highlight_id(highlight_id, api_user=ctx)
                    if not data:
                        raise ValueError
                await services.redis.set(key, data.to_bytes(), ex=43200)
            highlight = data.reels_media[0]
            resp = InstagramHighlightResponse(
                id=highlight_id,
                created_at=highlight.created_at,
                latest_reel_media=highlight.latest_reel_media,
                media_count=len(highlight.items),
            )
            for item in highlight.items:
                if item.video_versions:
                    is_video = True
                    media_url = item.video_versions[0].url
                    filename, task = services.insta.start_render(media_url, "Instagram", suffix=".mp4", force=force)
                else:
                    is_video = False
                    media_url = item.image_versions2.candidates[0].url
                    filename, task = services.insta.start_render(media_url, "Instagram", suffix=".jpg", force=force)
                media = InstagramHighlightMediaItem(taken_at=item.taken_at, is_video=is_video, url=media_url_from_request(filename))
                resp.items.append(media)

        return resp


@router.get("/reels", name="Download Reels", response_model=IgReelsIndexRaw, include_in_schema=False)
async def download_reels_index(request: Request):
    async with services.verify_token(request), asyncio.timeout(30):
        return await _download_reels_index()


@rcache(ttl="2h", key="instaprofile:{username}")
async def get_profile_info(username: str) -> InstagramProfileModelResponse:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    async with services.page_holder.borrow_page() as page:

        async def find_user(r: PlaywrightRequest):
            if r.url == f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}":
                if fut.done():
                    return
                if r.redirected_to:
                    return

                resp = await r.response()
                if "json" in resp.headers["content-type"]:
                    body = await resp.body()
                    body = orjson.dumps(orjson.loads(body))
                    with suppress(asyncio.InvalidStateError):
                        fut.set_result(body)

            if r.url == f"https://www.instagram.com/{username}":
                with suppress(PlaywrightError):
                    if fut.done():
                        return
                    await asyncio.sleep(0.5)
                    resp = await r.response()
                    await resp.finished()
                    body_text = await page.text_content("body")
                    if "The link you followed may be broken" in body_text:
                        fut.set_result(False)

                    html_data = await page.content()

                    result = await extract_json_tag(html_data, "biography_with_entities")
                    if result:
                        log.success("Found {} userdata via extract_json_tag", username)
                        data = orjson.dumps({"data": {"user": orjson.loads(result)}})
                        fut.set_result(data)

        page.on("request", find_user)
        try:
            await page.goto(f"https://www.instagram.com/{username}", wait_until="domcontentloaded")
            async with asyncio.timeout(12):
                data = await fut
            if data is False:
                log.warning("Returning invalid user for {}", username)
                return None
        except TimeoutError:
            return None
        except PlaywrightError as e:
            log.error("Unknown playwright error {}", e)
            return None
        finally:
            page.remove_listener("request", find_user)
    data = orjson.loads(data)
    profile = await InstagramProfileModel.from_web_info_response(data)
    final = InstagramProfileModelResponse(**profile.dict())
    final.created_at = time.time()
    if img_url := profile.profile_pic_url_hd:
        final.avatar_filename, task = services.start_render(img_url, "Instagram", suffix=".jpg")
    if profile.edge_owner_to_timeline_media:
        for item in profile.edge_owner_to_timeline_media.edges:
            pi = UserPostItem(**item.node.dict())
            pi.url = f"https://www.instagram.com/p/{pi.shortcode}"
            if item.node.edge_media_to_caption.edges:
                pi.title = item.node.edge_media_to_caption.edges[0].node.text
            final.post_items.append(pi)
    return final


@router.get(
    "/{username}",
    name="Get Instagram User",
    description="Fetch an Instagram user's profile with full metadata.",
    response_model=InstagramProfileModelResponse,
)
async def fetch_ig_user(
    request: Request,
    username: str,
    force: bool = Query(False, description="Force refresh or use cached result"),
):
    async with services.verify_token(request):
        api_user = api_username_var.get()
        try:
            username = validate_instagram_username(username)
        except ValueError as e:
            log.error("Requested to load an invalid username {}", username)
            return UJSONResponse(str(e), HTTP_404_NOT_FOUND)
        key = f"instaprofile:{username}"
        async with services.locks[key]:
            if force:
                await rcache.delete(key)
            if api_user == "Bleed" and await services.redis.get("disable_bleed_ig"):
                return UJSONResponse("Concurrency limit reached", HTTP_429_TOO_MANY_REQUESTS)
            cached = await get_profile_info(username)
            if not cached:
                expire = await redis.expiretime(key)
                delta = int(time.time() - expire)
                return UJSONResponse(f"Username {username} is negative cached. Expires in {delta} seconds", 404)
            cached.avatar_url = media_url_from_request(cached.avatar_filename)
            return cached
