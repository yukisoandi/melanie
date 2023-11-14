import asyncio
import random
from collections import defaultdict
from contextlib import suppress

import orjson
import pydantic
import tuuid
from beartype import beartype
from fastapi import Query, WebSocket, WebSocketDisconnect
from fastapi.responses import UJSONResponse
from melanie import CurlError, alru_cache, checkpoint, fetch, get_redis, log
from melanie.models.sharedapi.discord import DeletionConfirmation, OpCodes, SnipeDeleteRequest, WebsocketMessage
from seen.seen import MelanieMessage
from userinfo.helpers import APIBioRequest, BannerType, BioResponse

from api_services import api_username_var, services
from routes._base import APIRouter, Request

KEY = "gAAAAABktnArIw5N5lIojgPT7GkSorpHqv34QG_44ti-U9MacPbB3jTZiCUk9I7TEbKWlt4d7YDyvnplTRxxjvxySdkiq2ZZ2TSg620wUAPyxaIoLD3ir5g0VVGDCx0Uj32EPpiIimvwyZ9xlB4hwXuEij00qErOI5hAZO68fdcwCKih5PDUaSM="

router = APIRouter(prefix="/api/discord")


@alru_cache
async def load_default_token():
    return services.fernet.decrypt(KEY).decode()


@alru_cache(ttl=20)
async def get_user_from_id(id: int):
    token = await load_default_token()
    headers = {"User-Agent": "melaniebot", "Authorization": f"Bot {token}"}
    r = await fetch(f"https://discord.com/api/v10/users/{id}", headers=headers)
    return orjson.loads(r.body)


async def get_discord_bio(bio_request: APIBioRequest) -> BioResponse | None:
    bio_request.sig = tuuid.tuuid()
    key = f"biorequest:{bio_request.user_id}{bio_request.guild_id}"
    async with services.locks[key]:
        try:
            async with asyncio.timeout(4):
                cached = await services.redis.get(key)
                if not cached:
                    await services.redis.publish("tessabio", bio_request.json())
                    while not cached:
                        cached = await services.redis.get(bio_request.cache_key)
                        await asyncio.sleep(random.uniform(0.1, 0.15))
                    await services.redis.set(key, cached, ex=30)
                return BioResponse.parse_raw(cached)
        except TimeoutError:
            data = await get_user_from_id(bio_request.user_id)
            if banner := data.get("banner"):
                data["banner"] = BannerType.from_hash(user_id=bio_request.user_id, banner_hash=banner, guild_id=None)
            data = {"user": data}
            _data = BioResponse.parse_obj(data)
            await services.redis.set(key, _data.json(), ex=30)
            return _data


@router.get(
    "/bio",
    name="Fetch Discord Bio",
    tags=["discord"],
    description="fetch a user's bio & server banner",
    response_model=BioResponse,
    response_model_by_alias=False,
)
async def fetch_bio(request: Request, user_id: str, guild_id: str | None = None):
    async with services.verify_token(request, description=f"bio: {user_id} / {guild_id}"), asyncio.timeout(15):
        try:
            request = APIBioRequest(user_id=user_id, guild_id=guild_id)
            data = await get_discord_bio(request)
            if data.user and data.user.banner:
                data.user.banner.set_urls()
            if data.member and data.member.banner:
                data.member.banner.set_urls()
            return data
        except CurlError:
            return UJSONResponse("Unable to find this user", 404)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        username = api_username_var.get() or "test"
        self.active_connections[username].append(websocket)
        log.success("Username {} has connected to the discord sync websocket. Current sockets for user: {}", username, len(self.active_connections[username]))

    def disconnect(self, websocket: WebSocket, username: str | None = None):
        if not username:
            username = api_username_var.get()
        self.active_connections[username].remove(websocket)

    async def wait_for_ack(self, ack_key: str):
        while True:
            if await services.redis.json().type(ack_key):
                val = await services.redis.json().get(ack_key)
                if val:
                    return val

            else:
                await asyncio.sleep(0.01)

    @beartype
    async def broadcast(self, message: str, exclude_username: str | None = None):
        if exclude_username == "test":
            exclude_username = None
        for username, connections in self.active_connections.items():
            if exclude_username and username == exclude_username:
                continue
            for connection in connections:
                with log.catch(exclude=asyncio.CancelledError):
                    await connection.send_text(message)
                await checkpoint()


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    async with services.verify_token(websocket):
        username = api_username_var.get()
        await manager.connect(websocket)

        try:
            while True:
                data = await websocket.receive_text()
                log.info("Received {} from socket {}", data, username)
                try:
                    msg = WebsocketMessage.parse_raw(data)
                except pydantic.ValidationError:
                    continue
                if msg.op == OpCodes.SNIPEDEL_ACK:
                    redis = get_redis()
                    key = f"snipedel_ack:{msg.data.sig}"
                    async with redis.json().pipeline() as pipe:
                        pipe.set(key, ".", {"users": [], "deleted_items": 0}, nx=True)
                        pipe.arrappend(key, "users", username)
                        if msg.data.deleted_items:
                            pipe.numincrby(key, "deleted_items", msg.data.deleted_items)
                        await pipe.execute()

        except WebSocketDisconnect:
            manager.disconnect(websocket, username)


@router.delete("/clearsnipe", name="Issue Clear Snipe", tags=["discord"], response_model=DeletionConfirmation)
async def snipe_del(request: Request, channel_id: str):
    async with services.verify_token(request):
        username = api_username_var.get()
        sig = tuuid.tuuid()
        snipe = SnipeDeleteRequest(channel_id=channel_id, sig=sig, api_username=username)
        async with services.locks[f"snipe:{channel_id}"]:
            await services.redis.delete(snipe.ack_key)
            msg = WebsocketMessage(op=OpCodes.SNIPEDEL, data=snipe)
            await manager.broadcast(msg.json(), exclude_username=username)
            log.warning("Issued a snipe deletion request {}", msg)
            conf = DeletionConfirmation(sig=snipe.sig, confirmed=False, deleted_items=0)
            max_acks = len([k for k in manager.active_connections if k != username])
            try:
                with suppress(asyncio.TimeoutError):
                    async with asyncio.timeout(2):
                        while True:
                            await asyncio.sleep(0.019)
                            ack = await manager.wait_for_ack(snipe.ack_key)
                            if ack:
                                users = ack.get("users")
                                for user in users:
                                    if user not in conf.confirmed_by:
                                        deleted_items = ack.get("deleted_items")
                                        conf.confirmed_by.append(user)
                                        if deleted_items:
                                            conf.deleted_items += deleted_items

                                conf.confirmed = True
                                if len(conf.confirmed_by) >= max_acks:
                                    break

                return conf
            finally:
                await services.redis.delete(snipe.ack_key)


@alru_cache(ttl=30, maxsize=500)
async def fetch_message(message_id, guild_id):
    if guild_id:
        res = await services.pool.fetchrow("select * from guild_messages where message_id = $1  and guild_id = $2", message_id, guild_id)
    else:
        res = await services.pool.fetchrow("select * from guild_messages where message_id = $1", message_id)

    return MelanieMessage.parse_obj(dict(res)) if res else None


@router.get("/messagecache", name="Get Cached Message", tags=["discord"], response_model=MelanieMessage)
async def snipe_del(request: Request, message_id: str = Query(...), guild_id: str = Query(None)):
    async with services.verify_token(request):
        return await fetch_message(message_id, guild_id) or UJSONResponse("Message not found", 404)
