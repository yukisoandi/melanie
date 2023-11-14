import asyncio
from contextlib import suppress

import orjson
import pydantic
from fastapi.responses import UJSONResponse
from melanie.models.sharedapi.snap_profile import SnapPublicProfileModel
from melanie.models.sharedapi.snapchat import SnapDataRaw, SnapProfileResponse
from melanie.redis import rcache

from api_services import services
from routes._base import APIRouter, Request

router = APIRouter()


@rcache(ttl="5m")
async def get_snap_user(username: str) -> tuple[SnapDataRaw, dict]:
    async with services.page_holder.borrow_page() as page:
        r = await page.goto(f"https://www.snapchat.com/add/{username}", wait_until="domcontentloaded")
        if r.status == 404:
            return None, None

        data = await page.evaluate("__NEXT_DATA__")
        data = orjson.loads(orjson.dumps(data))
        return SnapDataRaw.parse_obj(data), data


@router.get("/api/snap/{username}", name="Get Snapuser", tags=["snapchat"], description="Fetch a snap user!", response_model=SnapProfileResponse)
async def fetch_snap_user(username: str, request: Request):
    async with services.verify_token(request, description="snapchat get"), asyncio.timeout(15):
        snap, data = await get_snap_user(username)
        if not snap:
            return UJSONResponse("Snap user not found", 404)
        resp = SnapProfileResponse(username=username)
        with suppress(AttributeError):
            resp.display_name = snap.props.page_props.user_profile.user_info.display_name
        with suppress(AttributeError):
            resp.snapcode_image_url = snap.props.page_props.page_links.snapcode_image_url
            resp.snapcode_image_url = resp.snapcode_image_url.replace("type=SVG", "type=PNG")
        with suppress(AttributeError):
            resp.one_click_url = snap.props.page_props.page_links.one_link_url
        with suppress(AttributeError):
            resp.bitmoji_url = snap.props.page_props.user_profile.user_info.bitmoji3d.avatar_image.url
        with suppress(AttributeError):
            resp.bitmoji_background_url = snap.props.page_props.user_profile.user_info.bitmoji3d.background_image.url
        with suppress(AttributeError):
            resp.share_image_url = snap.props.page_props.link_preview.facebook_image.url
        profile = None
        with suppress(pydantic.ValidationError):
            profile = SnapPublicProfileModel.parse_obj(data)

        if profile:
            with suppress(AttributeError):
                resp.subscriber_count = profile.props.page_props.user_profile.public_profile_info.subscriber_count
            with suppress(AttributeError):
                resp.bio = profile.props.page_props.user_profile.public_profile_info.bio
            with suppress(AttributeError):
                resp.profile_image_url = profile.props.page_props.link_preview.facebook_image.url
            with suppress(AttributeError):
                resp.display_name = profile.props.page_props.user_profile.public_profile_info.title

            with suppress(AttributeError):
                for story in profile.props.page_props.story.snap_list:
                    resp.story_media.append(story.snap_urls.media_url)

            with suppress(AttributeError):
                for hl in profile.props.page_props.spotlight_highlights:
                    for _snap in hl.snap_list:
                        resp.spotlight_media.append(_snap.snap_urls.media_url)

            with suppress(AttributeError):
                resp.hero_image_url = profile.props.page_props.user_profile.public_profile_info.square_hero_image_url

        return resp if resp.one_click_url else UJSONResponse("Snap user not found", 404)
