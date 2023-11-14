from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from random import uniform
from typing import TYPE_CHECKING

import orjson
from aiomisc.backoff import asyncretry
from fastapi import HTTPException
from filetype import guess_mime
from loguru import logger as log
from melanie import (
    AsyncPath,
    CurlError,
    MelanieRedis,
    aiter,
    alru_cache,
    borrow_temp_file,
    capturetime,
    get_curl,
    get_filename_from_url,
    jsondumps,
    jsonloads,
    snake_cased_dict,
    spawn_task,
    timeout,
    url_to_mime,
)
from melanie.helpers import extract_json_tag
from melanie.models.sharedapi.instagram import InstagramCarouselMediaResponse, InstagramPostItem, InstagramPostResponse, InstagramUserResponse
from melanie.models.sharedapi.instagram.post import InstagramPostModelRaw
from melanie.models.sharedapi.likes import RawUserLikesResponse
from melanie.redis import rcache
from playwright._impl._api_types import Error as PlaywrightError
from runtimeopt import DEBUG
from xxhash import xxh32_hexdigest

PlaywrightError = PlaywrightError
if TYPE_CHECKING:
    from playwright.async_api import BrowserContext
    from playwright.async_api import Request as PlaywrightRequest

    from api_services import Services
    from launch import BrowserContextHolder


class TimedEvent(asyncio.Event):
    async def wait(self, wait_timeout=None) -> bool:
        if not timeout:
            return await super().wait()

        try:
            async with asyncio.timeout(wait_timeout):
                return await super().wait()
        except TimeoutError:
            return False


def kill_quiet(proc):
    with suppress(ProcessLookupError):
        proc.kill()


@alru_cache
async def get_standard_avatar() -> bytes:
    curl = get_curl()
    r = await curl.fetch("https://hurt.af/gif/standard_ig_avatar.jpg")
    return r.body


def media_url_from_request(filename: str | None = None, direct: bool = False) -> str:
    from api_services import ORIGIN_VAR, api_username_var

    if not direct and (username := api_username_var.get()):
        if "montel" in username.lower():
            username = "Melanie"
        if username == "Bleed":
            return f"https://m.bleed.bot/{filename}"
        if username == "Melanie":
            return f"https://cache.hurt.af/{filename}"

    origin = ORIGIN_VAR.get()
    name = f"{origin}/media/{filename}"
    name = name.replace("/media/media/", "/media/")
    if not DEBUG:
        name = name.replace("http://", "https://")
    if not DEBUG:
        name = name.replace("dev.melaniebot.net/media/", "cache.hurt.af/")
    return name


class BrowserDataExtractor:
    def __str__(self) -> str:
        return self.__class__.__name__

    def __init__(self, services: Services, browser: BrowserContext, redis: MelanieRedis, page_holder: BrowserContextHolder) -> None:
        self.active_tasks = []
        self.services = services

        self.limit_cond = asyncio.Condition()
        self.s = services
        self.browser = browser
        self.page_holder: BrowserContextHolder = page_holder
        self.redis = redis
        self.htx = services.htx
        self.axel_sem = asyncio.BoundedSemaphore(16)
        self.limiter = asyncio.Semaphore(34)

    def set_event(self, task: asyncio.Task):
        task.event.set()

    async def pre_request(self, url) -> tuple[int, str]:
        async with self.htx.stream("GET", url, headers={"Range": "bytes=0-1"}) as stream:
            total = int(stream.headers["Content-Range"].split("/")[-1])
            return total, str(stream.url)

    def make_parts(self, total_size, url, limit):
        url = str(url)
        part_length = total_size // limit
        tasks = []
        for i in range(limit):
            start = i * part_length
            end = (i + 1) * part_length - 1 if i < limit - 1 else total_size - 1
            t = self.fetch_part(url, i, start, end)
            tasks.append(t)
        return tasks

    async def assemble_parts(self, parts: list[tuple[int, bytearray]]):
        final = bytearray()
        parts = sorted(parts, key=lambda x: x[0])
        for _index, buf in parts:
            final.extend(buf)
        return bytes(final)

    @asyncretry(max_tries=3, pause=0.1)
    async def fetch_part(self, url, index, start, end):
        buf = bytearray()
        async with self.services.aio.get(url, headers={"Range": f"bytes={start}-{end}"}) as r:
            async for chunk, _ in r.content.iter_chunks():
                buf.extend(chunk)
            return index, buf

    async def fetch_parallel(self, url, limit) -> bytearray:
        stats, resovled_url = await self.pre_request(url)
        tasks = []
        async with asyncio.TaskGroup() as tg:
            for t in self.make_parts(stats, resovled_url, limit):
                tasks.append(tg.create_task(t))
                await asyncio.sleep(0.001)
        log.info("Created {} parts", len(tasks))

        return await self.assemble_parts([await t for t in tasks])

    def unlocked(self):
        return not self.limiter.locked()

    def start_render(
        self,
        url,
        prefix,
        prekey=None,
        filename=None,
        download_data=None,
        suffix=None,
        force=False,
        passive=False,
    ) -> tuple[str, asyncio.Task]:
        url = str(url)
        expire = 691200
        from api_services import pending_tasks

        _name = get_filename_from_url(url)

        if not suffix:
            suffix = Path(_name).suffix
            if suffix == ".heic":
                suffix = ".jpg"
        if not filename:
            if not prekey:
                prekey = _name
            filename = f"{prefix}{xxh32_hexdigest(f'{prekey}')}{suffix}"
        filename = filename.replace(".jpeg", ".jpg")
        path = AsyncPath(filename)
        if not passive:
            self.services.active_renders[path.name] = asyncio.Event()
        if url == "/None":
            return "None", asyncio.create_task(asyncio.sleep(0))
        task = spawn_task(
            self.background_render(url=url, target=path.name, download_data=download_data, force=force, expire=expire, passive=passive),
            self.active_tasks,
        )

        if not passive:
            task.event = self.services.active_renders[path.name]
            task.add_done_callback(self.set_event)

        else:
            _tasks = pending_tasks.get()
            _tasks.append(task)
            task.add_done_callback(_tasks.remove)

        task.passive = passive
        return path.name, task

    async def fetch_standard(self, url):
        curl = get_curl()
        try:
            r = await curl.fetch(url)
            return bytes(r.body)
        except CurlError as e:
            if e.code == 410:
                log.warning("HTTP GONE for {} - Returning standard avatar", url)
                return await get_standard_avatar()
            log.warning("Curl failed: {} - retrying...", e)
            r = await self.services.htx.get(url)
            r.raise_for_status()
            return bytes(r.content)

    async def axel_fetch(self, url: str) -> bytes:
        async with borrow_temp_file() as tmp, self.axel_sem:
            proc = await asyncio.create_subprocess_exec(
                "axel",
                *["-n", "3", "--quiet", "--output", str(tmp), url],
                stdout=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return await tmp.read_bytes()

    async def background_render(
        self,
        url: str,
        target,
        download_data: bytes | str,
        force: bool = False,
        expire: int = 691200,
        passive: bool = False,
    ):
        if passive:
            return await self.redis.exhset("api_passive_url", target, url, ex=86400)
        async with self.services.locks[f"render_{target}"], self.limiter, asyncio.timeout(32):
            if not download_data and not url:
                msg = "Needs download_data or url set"
                raise ValueError(msg)
            if not force and not DEBUG:
                cached = await self.services.get_cached_target(target)
                if cached:
                    if mime := guess_mime(cached):
                        return True
                    else:
                        log.warning("Mime is invalid for {}", target)
            if download_data:
                await self.services.save_target(target, download_data, expire)
                return len(download_data)
            else:
                with capturetime(f"Render: {target}"):
                    if url.startswith("https://scontent.cdninstagram.com/v/") and "mp4" in url:
                        download_data = await self.axel_fetch(url)
                    else:
                        download_data = await self.fetch_standard(url)
                    if not download_data:
                        raise ValueError("no data")
                    mime = guess_mime(download_data)
                    if not mime:
                        raise ValueError(f"Downloaded an invalid payload for {target}")
                    await self.services.save_target(target, download_data)
                    return len(download_data)

    async def get_insta_post(self, url: str) -> InstagramPostResponse:
        share_url = url
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        data = None

        async def find_shortcode_info(r: PlaywrightRequest):
            if fut.done():
                return
            if "https://www.instagram.com/api/v1/" in r.url and "info" in r.url:
                resp = await r.response()
                if resp:
                    body = await resp.body()
                    body = orjson.loads(body)
                    if body:
                        fut.set_result(orjson.dumps(body))
            if r.url == url:
                if fut.done():
                    return

                @asyncretry(max_tries=2, pause=1.2)
                async def get_html():
                    return None if fut.done() else await page.content()

                resp = await r.response()
                if resp:
                    html = await get_html()
                    if "The link you followed may be broken" in html:
                        fut.set_result(False)
                    data = await extract_json_tag(html, "xdt_api__v1__media__shortcode__web_info")
                    if data:
                        log.success("Post info found in HTML body")
                        fut.set_result(orjson.dumps(orjson.loads(data)["xdt_api__v1__media__shortcode__web_info"]))

        async with self.page_holder.borrow_page() as page:
            page.on("request", find_shortcode_info)
            try:
                await page.goto(url, wait_until="commit")
                data = await fut
                if not data:
                    return data
            except TimeoutError:
                return None
            finally:
                if not fut.done():
                    fut.cancel()
                page.remove_listener("request", find_shortcode_info)

        model = InstagramPostModelRaw.parse_raw(data)
        final = InstagramPostResponse()
        if not model.items:
            return None
        mi = model.items[0]
        if mi.user:
            final.author = InstagramUserResponse(
                username=mi.user.username,
                full_name=mi.user.full_name,
                is_private=mi.user.is_private,
                is_verified=mi.user.is_verified,
            )
            if mi.user.profile_pic_url:
                final.author.avatar_filename, task = self.start_render(mi.user.profile_pic_url, "Instagram", suffix=".jpg")

        for item in model.items:
            final_item = InstagramPostItem(**item.dict())
            if item.video_versions:
                target_url = item.video_versions[0]
                filename, task = self.start_render(target_url.url, "Instagram", suffix=".mp4")
                final_item.video_filename = filename
                final_item.is_video = True

            elif item.image_versions2:
                for choice in item.image_versions2.candidates:
                    _mime = url_to_mime(choice.url)
                    if _mime and "heic" not in _mime[0]:
                        target_url = choice.url
                        filename, _task = self.start_render(target_url, "Instagram", suffix=".jpg")
                        final_item.image_filename = filename
                        break

            if item.image_versions2:
                total = len(item.image_versions2.candidates)
                middle = total // 2

                filename, task = self.start_render(item.image_versions2.candidates[middle].url, "Instagram", suffix=".jpg")
                final_item.preview_image_filename = filename

            if item.carousel_media:
                for cm in item.carousel_media:
                    cm_final = InstagramCarouselMediaResponse(**cm.dict())
                    if cm.video_versions:
                        target_url = cm.video_versions[0]
                        filename, task = self.start_render(target_url.url, "Instagram", suffix=".mp4")
                        cm_final.filename = filename
                        cm_final.is_video = True
                    elif cm.image_versions2:
                        for choice in cm.image_versions2.candidates:
                            _mime = url_to_mime(choice.url)
                            if _mime and "heic" not in _mime[0]:
                                target_url = choice.url
                                filename, task = self.start_render(target_url, "Instagram", suffix=".jpg", passive=True)
                                cm_final.filename = filename
                                break
                        total = len(cm.image_versions2.candidates)
                        middle = total // 2 if total < 7 else 7
                        filename, task = self.start_render(cm.image_versions2.candidates[middle].url, "Instagram", suffix=".jpg", passive=True)
                        cm_final.preview_image_filename = filename

                    final_item.sidecars.append(cm_final)
                    final_item.sidecar_count = len(final_item.sidecars)

        final.items.append(final_item)
        final.num_results = len(final.items)
        final.share_url = share_url
        return final

    async def download_all_liked(self, username: str, number_of_items: int = 100):
        data = await self.batch_tiktok_download(
            url=f"https://www.tiktok.com/@{username}",
            waits_for_url="/api/favorite/item_list",
            number_of_tiktoks=number_of_items,
            wait_for_state="domcontentloaded",
            e2e_selector="[data-e2e='liked-tab']",
        )
        return RawUserLikesResponse.valid_load(data)

    async def download_user_videos(self, username: str, number_of_tiktoks: int = 100):
        return await self.batch_tiktok_download(
            url=f"https://www.tiktok.com/@{username}",
            waits_for_url="/api/post/item_list/",
            number_of_tiktoks=number_of_tiktoks,
            wait_for_state="commit",
            ultra_fast_scroll=False,
        )

    async def download_current_fyp(self, number_of_tiktoks: int = 100):
        return await self.batch_tiktok_download(
            url="https://www.tiktok.com/foryou?is_copy_url=1&is_from_webapp=v1",
            waits_for_url="/api/recommend/item_list",
            number_of_tiktoks=number_of_tiktoks,
            wait_for_state="commit",
            ultra_fast_scroll=True,
        )

    @rcache(ttl="2h")
    async def batch_tiktok_download(
        self,
        url: str,
        waits_for_url: str,
        wait_for_state: str,
        focus_selector: str | None = None,
        number_of_tiktoks: int = 1,
        click_selector: str | None = None,
        ultra_fast_scroll: bool = False,
        e2e_selector=None,
    ):
        key = f"tiktokfyp:{xxh32_hexdigest(''.join(str(i) for i in locals().values()))}"
        lock = self.services.locks[key]
        async with lock:
            media_list = []

            cached = {"tiktoks": media_list}
            event = TimedEvent()
            q = asyncio.Queue()
            _worker_lock = asyncio.Lock()

            async def process_item() -> None:
                while _worker_lock.locked():
                    body = await q.get()
                    event.set()
                    with log.catch():
                        if len(body) > 1024:
                            data = jsonloads(body)
                            async for item in aiter(data.get("itemList", []), delay=0, steps=5):
                                if len(media_list) > number_of_tiktoks:
                                    break
                                item = snake_cased_dict(item)
                                media_list.append(item)
                    q.task_done()

            async def handle_response_save(req: PlaywrightRequest) -> None:
                if waits_for_url in str(req.url):
                    body = await req.body()
                    q.put_nowait(body)

            async with self.page_holder.borrow_page() as page:
                page.on("response", handle_response_save)
                await page.goto(url, wait_until=wait_for_state)
                await page.bring_to_front()
                if focus_selector:
                    await page.focus(focus_selector)

                if click_selector:
                    await page.click(click_selector)

                if e2e_selector:
                    selector = await page.wait_for_selector(e2e_selector)
                    await selector.click()

                async def fast_scroll() -> None:
                    event.clear()
                    num_scrolls = 10 if ultra_fast_scroll else 1
                    while not event.is_set():
                        if "/api/post/item_list/" in waits_for_url:
                            scroll_max = 1200
                            scroll_min = 500
                            waits_for = 1
                        else:
                            scroll_max = 3500
                            scroll_min = 2000
                            waits_for = 0.3

                        await asyncio.gather(*[(page.mouse.wheel(0, uniform(scroll_min, scroll_max))) for _ in range(num_scrolls)])
                        await event.wait(waits_for)
                    log.success("TikTok feed detected in background request")

                async with _worker_lock:
                    self.loop.spawn_callback(process_item)
                    while len(media_list) < number_of_tiktoks:
                        log.warning("Continuing to scroll.. only {}/{} so far", len(media_list), number_of_tiktoks)
                        try:
                            async with asyncio.timeout(30):
                                await lock.reacquire()
                                await fast_scroll()
                            await q.join()
                        except TimeoutError:
                            break
                    if not cached:
                        raise HTTPException(404, "Rendered no tiktoks")

                    # ok
                    await q.join()
                    await self.redis.set(key, jsondumps(cached), ex=10)
                    return cached
