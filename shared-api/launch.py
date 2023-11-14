from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import aiohttp
import distributed
import httpx
import humps
import msgspec
import orjson
import psutil
import tekore as tk
import tornado.process
from aiomisc.utils import fast_uuid4
from async_lru import alru_cache
from boltons.funcutils import dir_dict
from cashews import cache
from distributed.core import Server
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, UJSONResponse
from fastapi.routing import APIRoute
from filetype import guess_mime
from guardpost.jwts import InvalidAccessToken
from loguru import logger as log
from melanie import GLOBAL_DASK, BaseModel, capturetime, default_lock_cache, fetch, get_redis, spawn_task
from melanie.core import url_to_mime
from melanie.curl import DEFAULT_HEADERS, AwsRequestSigner, CurlError, get_curl
from melanie.models.sharedapi.apiconfig import settings
from melanie.models.sharedapi.other import GitCommitInfo
from melanie.redis import rcache
from multidict import CIMultiDict
from runtimeopt import DEBUG
from soundcloud import SoundCloud
from starlette.responses import HTMLResponse
from tornado.curl_httpclient import CurlError
from tornado.ioloop import IOLoop
from uvicorn.protocols.utils import get_path_with_query_string

from api_services import request_id_var, services
from browser import BrowserContextHolder
from routes import _all_routes

_is_ready = asyncio.Event()
_docs_html: str = pathlib.Path("docs/index.html").read_text()


class ORJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_SERIALIZE_UUID | orjson.OPT_INDENT_2)


async def launch(app: FastAPI, stack: AsyncExitStack) -> None:
    services.proc = psutil.Process()
    import routes

    tornado.process.Subprocess.initialize()
    services.loop = asyncio.get_running_loop()
    services.ioloop = IOLoop.current()
    tasks = []
    if DEBUG:
        rcache.setup("mem://?size=1000000")
    else:
        rcache.setup(
            "redis://melanie.melaniebot.net",
            secret=None,
            enable=True,
            suppress=True,
            max_connections=100,
            retry_on_timeout=True,
        )
    cache.setup("mem://?size=100000", secret=None, pickle_type="null", check_repr=False, digestmod=None)
    services.dask = distributed.Client(os.environ["DASK_HOST"], asynchronous=True, name="shared-api")
    await stack.enter_async_context(services.dask)
    spawn_task(services.setup_db(), tasks)
    services.app = app
    default_headers = CIMultiDict(**DEFAULT_HEADERS)
    default_headers["user-agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36"
    services.page_holder = BrowserContextHolder(app.stack)
    services.locks = default_lock_cache()
    services.aio = aiohttp.ClientSession(
        cookies=aiohttp.CookieJar(),
        headers=default_headers,
        raise_for_status=True,
        connector=aiohttp.TCPConnector(resolver=aiohttp.AsyncResolver(loop=services.loop)),
    )
    services.htx = httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        http2=True,
        cookies=httpx.Cookies(),
        limits=httpx.Limits(max_connections=500, max_keepalive_connections=200),
        timeout=httpx.Timeout(90),
    )
    await stack.enter_async_context(services.aio)

    spawn_task(services.load_known_services_keys(), tasks)
    GLOBAL_DASK["client"] = services.dask

    class S3Curl:
        sign = AwsRequestSigner("", "a3bnV1ulGNSfZvMFjmCR", "Bts3vwtrDZAY8N6YawOGtz2VX3WdGo3yXiGhjH2i", "s3")

        @staticmethod
        async def put_object(bucket: str, key: str, payload: bytes, content_type=None, timeout=30) -> tuple[bytes, dict]:
            assert isinstance(payload, bytes)
            content_hash = hashlib.sha256(payload).hexdigest()
            if not content_type:
                content_type = await guess_mime(payload)
            target = f"https://{bucket}.melaniebot.net/{key}"
            headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}
            headers |= S3Curl.sign.sign_with_headers("PUT", target, headers, content_hash)
            async with asyncio.timeout(timeout):
                r = await fetch(target, headers=headers, method="PUT", body=payload)
                return r.code

    async def upload_schema():
        await _is_ready.wait()

        with capturetime("Schema uploads"):
            version = await GitCommitInfo.current_commit_str()
            app.version = " " + version
            schema = app.openapi()
            await S3Curl.put_object("docs", "openapi.json", orjson.dumps(schema), "application/json"),
            await S3Curl.put_object("docs", "openapi.yaml", msgspec.yaml.encode(orjson.loads(orjson.dumps(schema))), "text/yaml"),
            log.warning("Set the app version to {}", app.version)

    spawn_task(upload_schema(), services.active_tasks)
    await services.page_holder.build()

    async def _build_audio_tokens() -> None:
        await _is_ready.wait()
        services.soundcloud = SoundCloud(os.environ["SOUNDCLOUD_ID"], os.environ["SOUNDCLOUD_SECRET"])
        services.tk_sender = tk.AsyncSender(client=services.htx)
        services.sp_cred = tk.Credentials(
            client_id=settings.sp_client_id,
            client_secret=settings.sp_client_secret,
            redirect_uri=settings.sp_redirect_url,
            sender=services.tk_sender,
            asynchronous=True,
        )

        async def _refresh_audio_tokens():
            while True:
                with log.catch(exclude=asyncio.CancelledError):
                    token = await services.sp_cred.request_client_token()
                    services.sp = tk.Spotify(token, sender=services.tk_sender, asynchronous=True)
                    if DEBUG:
                        return log.warning("Ending the spotify refresh loop since we're in debug")
                await asyncio.sleep(90)

        spawn_task(_refresh_audio_tokens(), services.active_tasks)

    if not DEBUG:
        services.ioloop.call_later(3600, services.shutdown_later)

    for t in asyncio.as_completed(tasks):
        await t

    spawn_task(services.load_password_file(), services.active_tasks)

    spawn_task(_build_audio_tokens(), services.active_tasks)
    spawn_task(rcache.ping(), services.active_tasks)

    handlers = {}

    import core
    import launch as _launch

    services.rpc_port = 22553
    checks = [services, services.insta, core, services.page_holder, _launch]

    for mod in dir_dict(routes).values():
        checks.append(mod)

    def serializer(func):
        async def process(*a, **ka):
            if asyncio.iscoroutinefunction(func):
                result = await func(*a, **ka)
            else:
                result = func(*a, **ka)
            if isinstance(result, BaseModel):
                return result.json()

            try:
                return orjson.dumps(result).decode()
            except orjson.JSONEncodeError:
                return str(result)

        return process

    for s in checks:
        for attr, value in dir_dict(s).items():
            if str(attr).startswith("_"):
                continue
            if callable(value) and attr not in handlers:
                handlers[attr] = serializer(value)

    services.server = Server(
        handlers,
        serializers=["dask", "pickle"],
        deserialize=["dask", "pickle"],
    )
    services.rpc_uri = f"tcp://:{services.rpc_port}"
    await services.server.listen(services.rpc_uri)
    await services.server.start()

    await stack.enter_async_context(services.server)
    log.success("Registered a total of {} RPC callbacks", len(handlers))
    log.success(services.server.address)
    services.start_render = services.insta.start_render
    _is_ready.set()


async def shutdown() -> None:
    log.success("Shutdown OK")


@asynccontextmanager
async def boot(app: FastAPI):
    async with AsyncExitStack() as stack:
        services.redis = get_redis()
        app.stack = stack
        await launch(app, stack)
        try:
            yield
        finally:
            await stack.aclose()
            await shutdown()


app = FastAPI(
    title="Melanie Data API ",
    redoc_url=None,
    docs_url=None,
    default_response_class=ORJSONResponse,
    description="A high performance & centrally cached API service for premium bots. ",
    lifespan=boot,
    servers=[{"url": "http://127.0.0.1:8091" if DEBUG else "https://dev.melaniebot.net", "description": "montreal"}],
    dependencies=[Depends(_is_ready.wait)],
)
app.stack: AsyncExitStack

app.services = services
services.app = app


@alru_cache(ttl=60)
async def download_passive(target):
    async with services.locks[f"passive_dl:{target}"], asyncio.timeout(40):
        if target not in services.passive_results:
            url = await services.redis.exhget("api_passive_url", target)
            if url:
                curl = get_curl()
                r = await curl.fetch(url)
                await services.save_target(target, r.body)
                mime = guess_mime(r.body)
                services.passive_results[target] = mime


@app.get("/media/{file_path:path}", include_in_schema=False)
async def media_fetch(request: Request, file_path: str):
    mime = None
    if file_path in services.active_renders:
        await services.active_renders[file_path].wait()

    try:
        mime = await download_passive(file_path)
    except (CurlError, TimeoutError) as e:
        return JSONResponse(
            f"Bad response fetching that asset. Error {e.message} {e.code}",
            status_code=e.code,
        )
    if not mime:
        mime = url_to_mime(str(request.url))[0]
    return Response(
        None,
        200,
        headers={"X-Accel-Redirect": file_path},
        media_type=mime,
    )


@app.get("/", include_in_schema=False)
async def elements_docs() -> HTMLResponse:
    return HTMLResponse(_docs_html)


@app.middleware("http")
async def http_access_logs(request: Request, call_next):
    request_id = str(fast_uuid4())
    request_id_var.set(request_id)
    services.current_request.set(request)
    status_code = "NA"
    try:
        if user_id := request.query_params.get("user_id"):
            redis = get_redis()
            if await redis.sismember("global_blacklist", str(user_id)):
                log.error("Rejecting blacklisted request for {} {}", user_id, request.url)
                return UJSONResponse("Invalid state", 403)
        try:
            response: Response = await call_next(request)
        except InvalidAccessToken:
            log.error("Returning invalid access token {}", str(request.base_url))
            return UJSONResponse("Unauthorized!", 403)
        status_code = response.status_code
        return response
    finally:
        if DEBUG:
            log.info("{} {} {} HTTP/{} ", status_code, request.method, get_path_with_query_string(request.scope), request.scope["http_version"])


for route in _all_routes:
    for x in route.routes:
        if isinstance(x, APIRoute):
            x.operation_id = humps.camelize(x.name.replace(")", "").replace("(", ""))
            x.response_model_exclude_none = True
    app.include_router(route)
