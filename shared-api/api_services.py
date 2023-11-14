from __future__ import annotations

import asyncio
import datetime
import io
import os
import shutil
import string
import sys
import threading
import time
import traceback
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import datetime  # noqa
from functools import partial
from typing import TYPE_CHECKING

import aiohttp
import arrow
import asyncpg
import asyncpg.connection
import dill
import httpx
import jwt
import msgspec
import orjson
import psutil
import shortuuid
import tekore as tk
import yarl
from aiofile import async_open
from anyio import Path as AsyncPath
from cryptography.fernet import Fernet
from distributed import Client
from distributed.core import Server
from fastapi import FastAPI, Request
from fastapi.websockets import WebSocket
from filetype import guess_mime
from guardpost.jwts import InvalidAccessToken, JWTValidator
from jose.backends.base import Key as JwkItem
from loguru import logger as log
from lru import LRU
from melanie import MelanieRedis, create_task, fetch, fmtseconds, spawn_task, threaded
from melanie.models.base import BaseModel
from melanie.models.sharedapi.instagram.instagram_post2 import InstagramCredentialItem
from melanie.models.sharedapi.other import KnownAccessServiceToken
from melanie.timing import capturetime
from playwright.async_api import Browser, Playwright
from pyarrow import fs
from soundcloud import SoundCloud
from tornado.curl_httpclient import CurlAsyncHTTPClient
from tornado.ioloop import IOLoop
from yt_dlp import YoutubeDL

from core import DEBUG, BrowserDataExtractor, media_url_from_request

if TYPE_CHECKING:
    from launch import BrowserContextHolder

AUD: str = os.getenv("AUD", "b2f13cf8406adb33cb634682eefac6fb01d2e173ba6c32f5294b98d058fcabdc")
CF_CERT_URL = "https://monty.cloudflareaccess.com/cdn-cgi/access/certs"
CF_SVC_HEADERS = {"Authorization": "Bearer MAinEMd7sGSxFQ1KcxMjYN-c3e8eX4DRhBEnfJ-l", "Content-Type": "application/json"}
CF_TOKEN_URL = "https://api.cloudflare.com/client/v4/accounts/e2e6b03ef41c4203bd2587e063452b21/access/service_tokens"

BYPASS_OPTIONS = {"verify_aud": False, "verify_iss": False, "verify_exp": False, "verify_nbf": False, "verify_signature": False, "verify_iat": False}

TRACKING_DDL = """create table if not exists public.api_requests
(
    created_at            timestamp        not null,
    route_name      text             not null,
    processing_time double precision not null,
    username        text             not null,
    user_id         text,
    args            jsonb,
    path_args       jsonb,
    body            jsonb,
    failed          boolean          not null,
    error           text,
    headers jsonb,
    ip              text
)
    using columnar;

"""


api_username_var: ContextVar[str] = ContextVar("api_username_var", default=None)
pending_tasks: ContextVar[list[asyncio.Task]] = ContextVar("pending_tasks", default=[])
request_id_var: ContextVar[str] = ContextVar("request_id_var", default=None)
ok_str = [",", ".", "/", "-", "_", "!", ":"]
REMOVE_PUNC = str(string.punctuation)
for i in ok_str:
    REMOVE_PUNC = REMOVE_PUNC.replace(i, "")

ORIGIN_VAR: ContextVar[str] = ContextVar("ORIGIN_VAR", default=None)


class LimitedAPIUsers(BaseModel):
    users: dict[str, list[str]] = {}


class APIRequestRecord(BaseModel):
    from datetime import datetime

    request_id: str
    created_at: datetime
    route_name: str
    processing_time: float
    username: str
    user_id: str | None
    args: str | None
    path_args: str | None
    body: str | None
    failed: bool = False
    error: str | None
    ip: str


class Services:
    _dask_keepalive_runner: asyncio.Task = None
    _dask: Client = None
    uuid = shortuuid.ShortUUID()
    server: Server
    passive_results: dict[str, str] = {}
    requests_passive_tasks = {}
    active_renders: dict[str, asyncio.Event]
    active_tasks: list[asyncio.Task] = []
    aio: aiohttp.ClientSession
    app_task: asyncio.Task
    built = False
    app: FastAPI
    cloudflare_keys: list[JwkItem] = None
    cookies = {}
    fernet = Fernet(os.environ["FERNET_KEY"])
    curl: CurlAsyncHTTPClient
    dask: Client = None
    debug: bool = DEBUG
    DEBUG: bool = DEBUG
    rpc_uri: str = None
    active_request_sem = asyncio.BoundedSemaphore(100)
    htx: httpx.AsyncClient
    insta: BrowserDataExtractor
    ioloop: IOLoop
    known_cf_service_tokens: dict[str, KnownAccessServiceToken] = {}
    loop: asyncio.AbstractEventLoop = None
    browser: Browser
    page_holder: BrowserContextHolder
    playwright: Playwright
    target_cache = LRU(500)
    redis: MelanieRedis
    soundcloud: SoundCloud
    sp_cred: tk.Credentials
    sp: tk.Spotify = None
    done_cache = {}
    pool: asyncpg.Pool
    startup_event: asyncio.Event = None
    validator: JWTValidator
    tk_sender: tk.AsyncSender
    track_sem = asyncio.BoundedSemaphore(12)
    username: str
    yt: YoutubeDL
    media_url_from_request = media_url_from_request
    locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    cachelocks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    instagram_credentials: dict[str, InstagramCredentialItem] = None
    limited_users: LimitedAPIUsers = LimitedAPIUsers()
    current_request: ContextVar[Request] = ContextVar("current_request")
    proc: psutil.Process
    closed = False
    fs = fs.LocalFileSystem(use_mmap=True)

    shutdown_thread = None

    async def shutdown_later(self):
        if self.closed:
            return

        self.closed = True
        log.warning("Shutting down now....")

        def shutdown_wait(proc: psutil.Process):
            log.warning("Shutdown thread started....")
            time.sleep(12)
            log.error("Wait exceeded. Sending process SIGILL")
            proc.kill()

        self.shutdown_thread = threading.Thread(target=shutdown_wait, args=[self.proc], daemon=True)
        self.shutdown_thread.start()

        async with asyncio.timeout(10):
            await self.page_holder.save()

        self.proc.terminate()

    async def execute_func(self, _func: bytes, **ka):
        func = dill.loads(_func)
        meth = partial(func, services=self, **ka)
        task = create_task(meth())
        return await task

    async def load_limited_users(self):
        file = AsyncPath("limited_api_users.yaml")
        data = await file.read_bytes()
        self.limited_users = LimitedAPIUsers.parse_obj(msgspec.yaml.decode(data))

    async def load_password_file(self) -> dict[str, InstagramCredentialItem]:
        file = AsyncPath("instagram_credentials.yaml")
        _data = await file.read_bytes()
        data = msgspec.yaml.decode(_data)

        final = {k: InstagramCredentialItem.parse_obj(v) for k, v in data.items()}
        for item in final.values():
            item.password = self.fernet.decrypt(item.password).decode()
            log.success("Decrypted password for {}", item.alias)

        self.instagram_credentials = final

    async def get_cached_target(self, target: str) -> bytes | None:
        buf = bytearray()

        with suppress(FileNotFoundError):
            async with async_open(f"api-cache/{target}", "rb") as f:
                async for chunk in f.iter_chunked(16000):
                    buf.extend(chunk)
            return bytes(buf)

    async def delete_cached_target(self, target: str) -> bytes | None:
        return await self.delete(f"api-cache/{target}")

    @threaded
    def move(self, src, dest):
        return shutil.move(src, dest)

    @threaded
    def delete(self, src):
        return shutil.rmtree(src, ignore_errors=True)

    async def save_target(self, target: str, data: bytes, ex: int = 691200) -> bool:
        buf = io.BytesIO(data)
        _tmp = f"api-cache/{target}.tmp"
        _file = f"api-cache/{target}"

        async with async_open(f"api-cache/{target}.tmp", "wb") as tmpfile:
            with buf:
                while True:
                    if chunk := buf.read(16000):
                        await tmpfile.write(chunk)
                    else:
                        break
        await self.move(_tmp, _file)
        return True

    async def optimize_target(self, target: str) -> tuple[str, bytes]:
        root = AsyncPath("api-cache")
        if await root.is_symlink():
            root = await root.readlink()
        with capturetime(f"optimize {target}"):
            async with self.locks[f"optim{target}"]:
                _file = AsyncPath(f"api-cache/{target}")
                new_target = _file.with_suffix(".webp")
                if await new_target.exists():
                    _data = await new_target.read_bytes()
                    if mime := guess_mime(_data):
                        self.target_cache[new_target.name] = _data
                        return new_target.name, self.target_cache[new_target.name]
                self.active_renders[new_target.name] = asyncio.Event()
                try:
                    opti_path = os.environ["OPTI_PATH"]
                    proc = await asyncio.create_subprocess_exec(
                        opti_path,
                        *[str(_file), "-o", str(root), "--webp", "--force"],
                        stdout=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        async with asyncio.timeout(30):
                            await proc.communicate()
                    finally:
                        with suppress(ProcessLookupError):
                            proc.kill()
                    self.target_cache[new_target.name] = await new_target.read_bytes()
                    return new_target.name, self.target_cache[new_target.name]
                finally:
                    self.active_renders[new_target.name].set()

    async def load_known_services_keys(self) -> None:
        self.known_cf_service_tokens = {}
        self.validator = JWTValidator(
            keys_url="https://monty.cloudflareaccess.com/cdn-cgi/access/certs",
            valid_audiences=["b2f13cf8406adb33cb634682eefac6fb01d2e173ba6c32f5294b98d058fcabdc"],
            valid_issuers=["https://monty.cloudflareaccess.com"],
        )
        r = await fetch(CF_TOKEN_URL, headers=CF_SVC_HEADERS)
        data = orjson.loads(r.body)
        for token in data["result"]:
            k = KnownAccessServiceToken.parse_obj(token)
            self.known_cf_service_tokens[k.client_id] = k

    @asynccontextmanager
    async def verify_token(self, request: Request, description=None, public: bool = False, **ka):
        start = time.perf_counter()
        request.cached = False
        request.api_username = None
        request.valid_token = None
        request.cached_body = None
        url2 = yarl.URL(str(request.url))
        ORIGIN_VAR.set(str(url2.origin()))
        if public:
            request.api_username = "public"
        if DEBUG:
            request.api_username = "debug"

        if cf_authed := request.headers.get("Cf-Access-Authenticated-User-Email"):
            request.api_username = cf_authed
        else:
            if "cf-access-jwt-assertion" not in request.headers:
                request.api_username = "test"

            elif not public and not DEBUG:
                token = request.headers["cf-access-jwt-assertion"]

                try:
                    token_data = await self.validator.validate_jwt(token)
                except InvalidAccessToken as e:
                    raw = jwt.decode(token, "", verify=False, algorithms=["RS256"], options=BYPASS_OPTIONS)
                    msg = f"Raw token: {orjson.dumps(raw, option=orjson.OPT_INDENT_2)}"
                    raise InvalidAccessToken(msg) from e

                try:
                    request.api_username = token_data["email"] if "email" in token_data else self.known_cf_service_tokens[token_data["common_name"]].name
                except KeyError:
                    log.info("Unknown token request {}", token_data)
                    request.api_username = "debug_unknown"

            if not request.api_username:
                request.api_username = "localroute"
        api_username_var.set(request.api_username)
        try:
            yield request
        finally:
            if DEBUG or isinstance(request, WebSocket):
                return
            request_id = request_id_var.get()
            exception = None
            duration = time.perf_counter() - start
            request.api_username.replace("<", "").replace(">", "")
            path2 = url2.path.replace("<", "").replace(">", "")
            path2 = url2.path.replace("<", "").replace(">", "")
            username = str(request.api_username)
            etype, e, tb = sys.exc_info()
            if etype:
                buf = io.StringIO()
                traceback.print_exc(file=buf)
                exception = buf.getvalue()

            if duration > 0.1:
                spawn_task(self.log_request(request, duration, start, request.api_username, exception, request_id), self.active_tasks)
            duration = time.perf_counter() - start
            if description:
                new_string = description.translate(str.maketrans("", "", REMOVE_PUNC))
                new_string = new_string.replace("<", "").replace(">", "")
                log.opt(colors=True).info(f"<magenta>{username}</magenta>: <white>{path2} ({new_string})</white> timed: {fmtseconds( duration)}")
            else:
                log.opt(colors=True).info(f"<magenta>{username}</magenta>: <white>{path2}</white> timed: {fmtseconds( duration)}")

    async def log_request(self, request: Request, duration: float, start_ts: float, username: str, error: Exception, request_id):
        await asyncio.sleep(0.09)
        _url = yarl.URL(str(request.url))
        user_id = None
        args = None
        body = None
        if _url.query and (_args := orjson.loads(orjson.dumps(dict(_url.query)))):
            user_id = _args.pop("user_id", None)
            args = orjson.dumps(_args).decode()

        path_args = orjson.dumps(dict(request.path_params)).decode() if request.path_params else None
        if request.method == "POST":
            body = await request.body()

            if body:
                body = orjson.loads(body)
                if body and isinstance(body, dict) and not user_id:
                    user_id = body.pop("user_id", None)
                body = orjson.dumps(body).decode()
            else:
                body = None

        if error:
            error = str(error)

        duration = round(duration, 5)
        headers = request.headers

        for h in ("Cf-Connecting-Ip", "X-Forwarded-For"):
            client_ip = headers[h]
            if client_ip:
                break
        entry = APIRequestRecord(
            request_id=request_id,
            created_at=arrow.utcnow().naive,
            route_name=request.scope["route"].operation_id,
            processing_time=duration,
            username=username,
            user_id=user_id,
            args=args,
            path_args=path_args,
            ip=client_ip,
            body=body,
            failed=bool(error),
            error=error,
        )
        request.client

        if entry.args == "{}":
            entry.args = None
        values = tuple(entry.dict().values())
        await self.pool.execute(
            "insert into api_requests(request_id, created_at, route_name, processing_time, username, user_id, args, path_args, body, failed, error,  ip) VALUES($1,$2, $3, $4, $5, $6, $7, $8, $9, $10, $11 , $12)",
            *values,
        )

    async def setup_db(self):
        self.pool = await asyncpg.create_pool(
            "postgresql://melanie:whore@melanie.melaniebot.net:5432/admin",
            min_size=1,
            max_size=30,
        )
        await self.pool.execute(TRACKING_DDL)


services = Services()
services.active_renders = {}
