import asyncio
import subprocess

import orjson
import pydantic
import regex as re
import requests
from async_lru import alru_cache
from boltons.iterutils import default_enter, remap
from fastapi.responses import UJSONResponse
from melanie import borrow_temp_file_sync, log, rcache, threaded, url_to_mime
from melanie.curl import get_curl
from melanie.models.sharedapi.pinterest.pinterest import PinterestProfileResponse
from melanie.models.sharedapi.pinterest.post import PinterestResponseModel
from melanie.models.sharedapi.pinterest.reverse import PinterestReverseResult
from melanie.models.sharedapi.pinterest.userinfo_api import PinterestUserinfoAPIResponse
from melanie.redis import rcache

from api_services import services
from core import media_url_from_request
from routes._base import APIRouter, Request

POST_RE = re.compile(
    r"(?x) https?://(?:[^/]+\.)?pinterest\.(?: com|fr|de|ch|jp|cl|ca|it|co\.uk|nz|ru|com\.au|at|pt|co\.kr|es|com\.mx|"
    r" dk|ph|th|com\.uy|co|nl|info|kr|ie|vn|com\.vn|ec|mx|in|pe|co\.at|hu|"
    r" co\.in|co\.nz|id|com\.ec|com\.py|tw|be|uk|com\.bo|com\.pe)/pin/(?P<id>\d+)",
)

router = APIRouter(tags=["pinterest"], prefix="/api/pinterest")


def url_map(payload: bytes, discard_keys=[]) -> tuple[str, list[asyncio.Task]]:
    obj = orjson.loads(payload)
    discard_keys = set(discard_keys)
    tasks = []

    def _enter(p, k, v):
        final = None
        if k == "video_list":
            final = {}
            for k2, v2 in v.items():
                if ".mp4" in v2["url"] and len(final) < 3:
                    final[k2] = v2
        return default_enter(p, k, final or v)

    def _visit(p, k, v):
        if k in discard_keys:
            return False
        if isinstance(v, str):
            mime = url_to_mime(v)[0]
            if mime:
                passive = "video" not in mime
                filename, task = services.insta.start_render(v, prefix="Pinterest", passive=passive)
                if passive:
                    tasks.append(task)
                return (k, media_url_from_request(filename))
            else:
                return (k, v)
        return k, v

    return remap(obj, visit=_visit, enter=_enter), tasks


@rcache(ttl="1h", key="pinpost:{url}")
async def fetch_pinterest_post(url: str) -> tuple[bool, PinterestResponseModel | int]:
    async with asyncio.timeout(25):
        try:
            ident = POST_RE.match(url).group("id")
        except (AttributeError, ValueError):
            return False, 404
        if not ident:
            return False, 404

        opt = {
            "options": {
                "field_set_key": "unauth_react_main_pin",
                "id": f"{ident}",
            },
        }

        param = {"data": orjson.dumps(opt).decode()}
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.164 Safari/537.36",
        }
        r = await services.htx.get("https://www.pinterest.com/resource/PinResource/get/", headers=headers, params=param, follow_redirects=True)
        if r.is_error:
            return False, r.status_code

        _data, tasks = url_map(
            r.content,
            discard_keys=[
                "enabled_advertiser_countries",
                "user_agent",
                "unauth_id",
                "client_context",
                "csp_nonce",
                "tracking_params",
            ],
        )

        if tasks:
            await asyncio.gather(*tasks)
        model = PinterestResponseModel.parse_obj(_data)
        return True, model


@router.get("/post", name="Get post", description="Fetcha pinterest post!", response_model=PinterestResponseModel)
async def pin_post_fetch(request: Request, url_or_id: str):
    if not url_or_id.startswith("https://www.pinterest.com") and len(url_or_id) < 32:
        url_or_id = f"https://www.pinterest.com/pin/{url_or_id}/"
    async with services.verify_token(request), services.locks[f"pinpost:{url_or_id}"]:
        ok, result = await fetch_pinterest_post(url_or_id)
        if not ok:
            return UJSONResponse("Error fetching the post", status_code=result)
        return result


@rcache(ttl="2m")
async def fetch_pinterest_user(username: str) -> PinterestProfileResponse | None:
    META_ATTRS = {
        "og:url": "url",
        "pinterestapp:about": "description",
        "pinterestapp:followers": "followers",
        "pinterestapp:following": "following",
        "pinterestapp:pins": "pins",
        "og:image": "avatar_url",
    }
    async with services.page_holder.borrow_page() as page:
        await page.goto(f"https://www.pinterest.com/{username}", wait_until="domcontentloaded")
        resp = PinterestProfileResponse(username=username)

        async def get_attr_value(attr: str, target: str) -> str | None:
            s = await page.query_selector(f'meta[property="{attr}"]')
            value = await s.get_attribute("content")
            setattr(resp, target, value)

        try:
            await asyncio.gather(*[get_attr_value(attr, target) for attr, target in META_ATTRS.items()])
        except AttributeError:
            return None
        if resp.avatar_url:
            curl = get_curl()
            r = await curl.fetch(resp.avatar_url, raise_error=False)
            if r.error:
                resp.avatar_url = None
            else:
                filename, task = services.insta.start_render(resp.avatar_url, download_data=r.body, prefix="pin")
                resp.avatar_url = media_url_from_request(filename=filename)
        return resp


async def userinfo(name):
    headers = {
        "authority": "www.pinterest.com",
        "accept": "application/json, text/javascript, */*, q=0.01",
        "accept-language": "en-US,en;q=0.6",
        "dnt": "1",
        "referer": "https://www.pinterest.com/",
        "sec-ch-ua": '"Brave";v="111", "Not(A:Brand";v="8", "Chromium";v="111"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
        "x-pinterest-appstate": "active",
        "x-pinterest-pws-handler": "www/[username].js",
        "x-pinterest-source-url": f"/{name}/",
        "x-requested-with": "XMLHttpRequest",
    }

    params = {"source_url": f"/{name}/", "data": '{"options":{"username":"USERNAME","field_set_key":"profile"},"context":{}}'.replace("USERNAME", name)}

    async with services.page_holder.borrow_page() as page:
        r = await page.request.get("https://www.pinterest.com/resource/UserResource/get/", params=params, headers=headers)
        data = PinterestUserinfoAPIResponse.parse_raw(await r.body())
        await r.dispose()
        return data


@router.get("/{username}", name="Get Pinterest user", description="Fetch a pinterest user!", response_model=PinterestProfileResponse, operation_id="getPinUser")
async def get_pin_user(request: Request, username: str):
    async with services.verify_token(request, description=f"pinterest get {username}"):
        return await fetch_pinterest_user(username) or UJSONResponse("User not found", 404)


@alru_cache
@threaded
def do_pinterest_search_api2(img_url: str):
    with borrow_temp_file_sync(extension=".jpg") as outfile:
        cmd_call = ["/usr/local/bin/ffmpeg", "-i", str(img_url), str(outfile)]
        subprocess.check_output(cmd_call, timeout=10)
        cookies = {
            "_b": '"AXIt4oa8ISFG2rFVwRUb5OWPtrIlGn67w77AocdE5cnngaZdDtLezPB85wcdVcAjsJE="',
            "_pinterest_ct": '"TWc9PSZZRmQ5akJOYzhYSjRzU0ZKSXIwa3p0RFRVQU9TekhrL0JiNkdhQ1pXWTJDQ3pxNHQ4WEgvVHNZcklPQUN2ZlRrMnJwT0Q2Wk94bU1FT3JOYVlQNDBjV0hLRXBXT0gxU2s0REFZcnpmQnpsND0mZzR4L242SDRHdnBDczRidjRFRmVQMUlvZXpNPQ=="',
            "_ir": "0",
        }

        headers = {
            "Host": "api.pinterest.com",
            "Connection": "keep-alive",
            "Accept": "application/json",
            "X-Pinterest-Device": "iPhone12,5",
            "Authorization": "Bearer MTQzMTU5NDo5MzEzMzA1MzUzNDk5NTE5NDk6OTIyMzM3MjAzNjg1NDc3NTgwNzoxfDE2OTU5NTc5Mzc6MC0tN2M4M2IzNGI3MzdjNDg0YmM2ZjZhZjM3ZTFmODlmMWM=",
            "X-B3-TraceId": "7f221854c2f2ccbc",
            "X-B3-SpanId": "ddb7e71c2f6a85a7",
            "X-Pinterest-InstallId": "27a13092970944abba54c3634175564e",
            "Accept-Language": "en-US",
            "X-Pinterest-AppState": "active",
            "X-Pinterest-App-Type-Detailed": "1",
            "User-Agent": "Pinterest for iOS/11.34.1 (iPhone12,5; 16.6.1)",
            "X-B3-ParentSpanId": "10871d6bf6d66b21",
            # requests won't add a boundary if this header is set when you pass files=
            # 'Content-Type': 'multipart/form-data',
        }

        files = {
            "x": (None, "0"),
            "fields": (
                None,
                "pin.{is_downstream_promotion,is_whitelisted_for_tried_it,description,comments_disabled,created_at,is_stale_product,is_video,promoted_is_max_video,link,id,pinner(),reaction_counts,top_interest,promoted_quiz_pin_data,domain_tracking_params,board(),promoter(),ad_data(),is_premiere,image_signature,auto_alt_text,story_pin_data(),ad_destination_url,is_promoted,sponsorship,image_square_url,native_creator(),videos(),virtual_try_on_type,destination_url_type,grid_title,is_year_in_preview,view_tags,is_scene,rich_summary(),aggregated_pin_data(),is_oos_product,is_ghost,category,should_preload,image_medium_url,dark_profile_link,is_full_width,call_to_action_text,additional_hide_reasons,ip_eligible_for_stela,shuffle_asset(),comment_count,promoted_is_quiz,ad_match_reason,is_unsafe_for_comments,is_eligible_for_aggregated_comments,is_eligible_for_related_products,origin_pinner(),is_unsafe,is_native,ad_targeting_attribution,ad_closeup_behaviors,source_interest(),question_comment_id,image_crop,shuffle(),shopping_mdl_browser_type,should_mute,shopping_flags,promoted_lead_form(),promoted_is_showcase,is_eligible_for_web_closeup,domain,story_pin_data,tracking_params,is_eligible_for_pdp_plus,mobile_link,share_count,cacheable_id,tracked_link,is_eligible_for_brand_catalog,done_by_me,is_shopping_ad,title,carousel_data(),type,attribution,is_repin,promoted_is_lead_ad,comment_reply_comment_id,should_open_in_stream,dominant_color,product_pin_data(),creative_types,embed(),alt_text,is_cpc_ad,promoted_ios_deep_link,repin_count,is_eligible_for_pdp,promoted_is_removable,music_attributions},board.{image_cover_url,layout,owner(),id,privacy,is_ads_only,followed_by_me,name,image_thumbnail_url},interest.{follower_count,id,key,type,name,is_followed},productmetadatav2.{items},itemmetadata.{additional_images},richpingriddata.{aggregate_rating,id,type_name,products(),site_name,display_cook_time,is_product_pin_v2,display_name,actions,mobile_app},aggregatedpindata.{collections_header_text,catalog_collection_type,pin_tags,id,is_shop_the_look,has_xy_tags,is_dynamic_collections,aggregated_stats,pin_tags_chips,slideshow_collections_aspect_ratio},pincarouselslot.{domain,details,id,title,link,image_signature,ios_deep_link,ad_destination_url},storypindata.{has_product_pins,page_count,id,has_virtual_try_on_makeup_pins,static_page_count,total_video_duration,has_affiliate_products,pages_preview,metadata,type},shuffle.{id,type,source_app_type_detailed},embed.{src,width,type,height},pincarouseldata.{id,carousel_slots,index},storypinpage.{blocks,style,layout,id,image_signature_adjusted,video_signature,image_signature,music_attributions,type,should_mute,video[V_HLSV3_MOBILE,V_HLS_HEVC,V_HEVC_MP4_T1_V2,V_HEVC_MP4_T2_V2,V_HEVC_MP4_T3_V2,V_HEVC_MP4_T4_V2,V_HEVC_MP4_T5_V2]},shuffleasset.{id,item_type,shuffle_item_image,pin()},storypinimageblock.{image_signature,block_style,type,block_type,text},storypinvideoblock.{text,block_style,video_signature,type,block_type,video[V_HLSV3_MOBILE,V_HLS_HEVC,V_HEVC_MP4_T1_V2,V_HEVC_MP4_T2_V2,V_HEVC_MP4_T3_V2,V_HEVC_MP4_T4_V2,V_HEVC_MP4_T5_V2]},user.{explicitly_followed_by_me,id,image_small_url,show_creator_profile,full_name,native_pin_count,username,first_name},video.{id,video_list[V_HLSV3_MOBILE,V_HLS_HEVC]},board.cover_images[200x],pin.images[345x,736x],interest.images[70x70,236x],pincarouselslot.images[345x,736x],imagemetadata.canonical_images[1200x,474x],storypinimageblock.image[1200x,345x,736x],storypinpage.image_adjusted[1200x,345x,736x],storypinpage.image[1200x,345x,736x]",
            ),
            "y": (None, "0"),
            "h": (None, "1"),
            "camera_type": (None, "0"),
            "search_type": (None, "0"),
            "source_type": (None, "0"),
            "crop_source": (None, "5"),
            "w": (None, "1"),
            "page_size": (None, "25"),
            "page_size": (None, "50"),
            "image": open(str(outfile), "rb"),
        }

        response = requests.post("https://api.pinterest.com/v3/visual_search/lens/search/", cookies=cookies, headers=headers, files=files)
        try:
            response.raise_for_status()
            if response.content:
                return PinterestReverseResult.parse_raw(response.content)
        except (requests.HTTPError, pydantic.ValidationError):
            return log.exception("Unable to load the search {} ")


@router.get("/reverse", name="Reverse Image search", description="Reverse search a photo on pinterest", response_model=PinterestReverseResult)
async def pin_reverse_search(request: Request, img_url: str):
    async with services.verify_token(request), services.locks[f"imgdl:{img_url}"]:
        return await do_pinterest_search_api2(img_url) or UJSONResponse("No results for that image", status_code=404)
