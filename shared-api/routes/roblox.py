import asyncio

import arrow
import httpx
from aiomisc.backoff import asyncretry
from fastapi.responses import UJSONResponse
from melanie import log, rcache
from melanie.models.sharedapi.roblox import BadgeItem, RobloxUserProfileResponse
from roblox import Client, UserNotFound
from roblox.thumbnails import AvatarThumbnailType
from roblox.utilities.exceptions import BadRequest

from api_services import services
from routes._base import APIRouter, Request

router = APIRouter()


@rcache(ttl="1h", key="robloxuser2:{username}")
async def fetch_roblox_user(username: str):
    async with httpx.AsyncClient(http2=True) as htx:
        resp = RobloxUserProfileResponse()
        client = Client(httpx_client=htx)
        try:
            user = await client.get_user_by_username(username)
        except (UserNotFound, BadRequest):
            return None
        resp.display_name = user.display_name
        resp.description = user.description
        resp.created = arrow.get(user.created).timestamp()
        resp.is_banned = user.is_banned
        resp.id = user.id
        resp.name = user.name
        resp.has_verified_badge = bool(user.__dict__["_data"]["hasVerifiedBadge"])

        @asyncretry(max_tries=2, pause=0.1)
        async def follower():
            resp.follower_count = await user.get_follower_count()

        @asyncretry(max_tries=2, pause=0.1)
        async def following():
            resp.following_count = await user.get_following_count()

        @asyncretry(max_tries=2, pause=0.1)
        async def presence():
            presence = await user.get_presence()
            resp.last_online = arrow.get(presence.last_online).timestamp()
            resp.presence = presence.user_presence_type.name
            if resp.presence == "in_game":
                resp.presence = "In game"
            resp.last_location = presence.last_location

        @asyncretry(max_tries=2, pause=0.1)
        async def headshots():
            thumbnails = await client.thumbnails.get_user_avatar_thumbnails(
                users=[user],
                type=AvatarThumbnailType.full_body,
                size=(420, 420),
            )
            resp.avatar_url = thumbnails[0].image_url
            if resp.avatar_url == "https://t3.rbxcdn.com/9fc30fe577bf95e045c9a3d4abaca05d":
                resp.avatar_url = None

        @asyncretry(max_tries=2, pause=0.1)
        async def badges():
            badges = await user.get_roblox_badges()
            resp.badges = []
            for badge in badges:
                resp.badges.append(BadgeItem(id=badge.id, name=badge.name, description=badge.description, image_url=badge.image_url))

        async def names():
            try:
                async for name in user.username_history():
                    resp.previous_names.append(str(name))
            except BadRequest:
                return log.warning("Bad roblox names request for {}", username)

        await asyncio.gather(
            names(),
            badges(),
            headshots(),
            presence(),
            following(),
            follower(),
        )

        return resp


@router.get("/api/roblox/{username}", name="Get Roblox user", tags=["roblox"], response_model=RobloxUserProfileResponse)
async def get_roblox_user(username: str, request: Request):
    async with services.verify_token(request, description=f"roblox {username}"):
        key = f"roblox:{username}"
        async with services.locks[key], asyncio.timeout(10):
            return await fetch_roblox_user(username) or UJSONResponse("Invalid user", 404)
