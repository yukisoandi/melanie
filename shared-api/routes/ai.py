import asyncio
import os
import random
from contextlib import suppress

import orjson
import xxhash
from aiomisc import asyncretry
from aiomisc.backoff import asyncretry
from fastapi.responses import FileResponse, Response, UJSONResponse
from filetype import guess_mime
from melanie import HttpUrl, alru_cache, get_curl, log, snake_cased_dict, url_concat
from melanie.models.sharedapi.ai import AIImageGenerationResponse, ImageEvaluationResult, make_ai_url
from melanie.models.sharedapi.apiconfig import settings
from melanie.models.sharedapi.vision import AzureOcrReadRaw, OCRReadResponse, OCRRquest, prepare_image
from starlette.status import HTTP_400_BAD_REQUEST

from api_services import services
from core import media_url_from_request
from routes._base import APIRouter, Query, Request

router = APIRouter(tags=["ai"], prefix="/api/ai")

MOD_URL: str = "https://melaniemodeus.cognitiveservices.azure.com/contentmoderator/moderate/v1.0/ProcessImage/Evaluate?CacheImage=true"
AI_KEY: str = os.environ["BUNNY_AI_KEY"]
MOD_KEY: str = os.environ["MOD_KEY"]
OCR_URL: str = "https://eastus.api.cognitive.microsoft.com/formrecognizer/documentModels/prebuilt-read:analyze?api-version=2023-07-31"


@alru_cache
@asyncretry(max_tries=3, pause=1)
async def evaluate_image_url(url: str) -> ImageEvaluationResult:
    headers = {"Content-Type": "application/json", "Ocp-Apim-Subscription-Key": MOD_KEY}
    data = {"DataRepresentation": "URL", "Value": url}
    curl = get_curl()
    r = await curl.fetch(MOD_URL, body=orjson.dumps(data), method="POST", headers=headers)
    data = snake_cased_dict(orjson.loads(r.body), discard_keys=["cache_id", "tracking_id", "status"])
    data["adult"] = data["is_image_adult_classified"]
    data["racy"] = data["is_image_racy_classified"]
    return ImageEvaluationResult.parse_obj(data)


@router.get(
    "/nsfw_check",
    name="Evaluate Image Safety",
    description="Returns probabilities of the image containing racy or adult content",
    operation_id="nsfwScan",
    response_model=ImageEvaluationResult,
)
async def screen_image(url: HttpUrl):
    async with asyncio.timeout(30):
        return await evaluate_image_url(url)


@alru_cache(ttl=400)
async def get_seed():
    return random.randint(1000, 9999)


@asyncretry(max_tries=2, pause=0.2)
async def _do_api_read(data) -> AzureOcrReadRaw:
    payload, _hashed = await prepare_image(data)
    headers = {"Content-Type": "application/octet-stream", "Ocp-Apim-Subscription-Key": settings.ocr_key}
    mime = guess_mime(payload)
    if not mime or "image" not in mime:
        msg = "Invalid image"
        raise ValueError(msg)
    r = await services.htx.post("https://eastus.api.cognitive.microsoft.com/vision/v3.2/read/analyze?language=en", data=payload, headers=headers)
    r.raise_for_status()
    target = r.headers["Operation-Location"]
    while True:
        await asyncio.sleep(0.3)
        r = await services.htx.get(target, headers=headers)
        r.raise_for_status()
        data = bytes(r.content)
        response = orjson.loads(r.content)
        status = response.get("status")
        if status == "failed":
            msg = f"Unable to encode {r.body.decode()}"
            raise ValueError(msg)
        elif status == "succeeded":
            return AzureOcrReadRaw.parse_obj(snake_cased_dict(orjson.loads(data)))
        else:
            log.warning("Continuing to wait for the result from OCR read...")
            await asyncio.sleep(0.75)
            continue


@alru_cache
async def perform_ocr_read(url) -> OCRReadResponse:
    async with services.aio.get(url) as r:
        image_data = await r.read()
    mime = guess_mime(image_data)
    if not mime or "image" not in mime:
        return None
    with suppress(ValueError):
        result = await _do_api_read(image_data)
    if result:
        response = OCRReadResponse()
        for page in result.analyze_result.read_results:
            for line in page.lines:
                response.lines.append(line.text)
        response.display_text = ""
        chunk = ""
        for line in response.lines:
            chunk += line
            chunk += " "
            if len(chunk) > 50:
                chunk += "\n"
                response.display_text += chunk
                chunk = ""
        if chunk:
            response.display_text += chunk
            return response
        return None
    return None


@router.post(
    "/ocr",
    name="Read Text from Image",
    description="Perform OCR on a text image. Responses may take up to 10 seconds to return!",
    response_model=OCRReadResponse,
)
async def make_ocr_request(post_request: OCRRquest, request: Request):
    async with services.verify_token(request), asyncio.timeout(65):
        return await perform_ocr_read(post_request.url) or UJSONResponse("Invalid media mime type. Should be image.", status_code=HTTP_400_BAD_REQUEST)


@alru_cache(ttl=120)
async def make_bunny_avatar(prompt: str, model: str, blueprint: str) -> str:
    seed = await get_seed()
    prompt = prompt.replace(" ", "-")
    url = make_ai_url(AI_KEY, f"/.ai/img/{model}/{blueprint}/{seed}/{prompt}.jpg", 3600, "https://melaniebot-dashboard.b-cdn.net")
    name = f"AiImage{xxhash.xxh32_hexdigest(f'{seed}{prompt}{blueprint}{model}')}.png"
    curl = get_curl()
    r = await curl.fetch(url)
    data = bytes(r.body)
    code = xxhash.xxh128_hexdigest(data)
    if code == "3c52a0f1abfc37b22325394b1d9bed6b":
        return None
    mime = guess_mime(data)
    if not mime or "image" not in mime:
        return None
    await services.save_target(name, data)
    filename, data = await services.optimize_target(name)
    return filename


@router.get(
    "/avatar",
    name="Create pixel avatar",
    description="Create a colorful pixel art totally avatar",
    response_model=AIImageGenerationResponse,
    operation_id="createPixelAvatar",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Return the JSON item or an image.",
        },
    },
)
async def generate_ai_avatar(request: Request, idea: str = Query(..., description="AI generation prompt", max_length=500)):
    async with services.verify_token(request):
        name = await make_bunny_avatar(prompt=idea, model="dalle-512", blueprint="demo-pixel-avatar")
        if not name:
            return UJSONResponse("Unable to generate that image", 404)
        if "Mac" in request.headers["User-Agent"]:
            return FileResponse(f"api-cache/{name}")
        return AIImageGenerationResponse(url=media_url_from_request(name), idea=idea)


@router.get(
    "/cyberpunk",
    name="Create cyberpunk avatar",
    description="Create a Cyberpunk avatar",
    response_model=AIImageGenerationResponse,
    operation_id="createCyberpunkAvatar",
)
async def generate_cyberpunk_avatar(request: Request, idea: str = Query(..., description="AI generation prompt", max_length=500)):
    async with services.verify_token(request):
        name = await make_bunny_avatar(prompt=idea, model="dalle-512", blueprint="demo-cyberpunk-avatar")
        if not name:
            return UJSONResponse("Unable to generate that image", 404)
        if "Mac" in request.headers["User-Agent"]:
            return FileResponse(f"api-cache/{name}")
        return AIImageGenerationResponse(url=media_url_from_request(name), idea=idea)


@router.get(
    "/creative",
    name="Create fantasy art avatar",
    description="Creative image generator",
    response_model=AIImageGenerationResponse,
    operation_id="createFantasyAvatar",
)
async def generate_fantasy_avatar(request: Request, idea: str = Query(..., description="AI generation prompt", max_length=500)):
    async with services.verify_token(request):
        name = await make_bunny_avatar(prompt=idea, model="sd21-512", blueprint="creative2")
        if not name:
            return UJSONResponse("Unable to generate that image", 404)
        if "Mac" in request.headers["User-Agent"]:
            return FileResponse(f"api-cache/{name}")
        return AIImageGenerationResponse(url=media_url_from_request(name), idea=idea)


@router.get(
    "/dalle-2",
    name="Create art with dalle-2",
    description="Creative dalle-2 generator",
    response_model=AIImageGenerationResponse,
    operation_id="createDalle2Art",
)
async def generate_dalle_2(request: Request, idea: str = Query(..., description="AI generation prompt", max_length=500)):
    async with services.verify_token(request):
        name = await make_bunny_avatar(prompt=idea, model="dalle-512", blueprint="default")
        if not name:
            return UJSONResponse("Unable to generate that image", 404)
        if "Mac" in request.headers["User-Agent"]:
            return FileResponse(f"api-cache/{name}")
        return AIImageGenerationResponse(url=media_url_from_request(name), idea=idea)


@alru_cache
@asyncretry(max_tries=2, pause=0.2)
async def segment_image_url(url: str) -> bytes:
    headers = {"content-type": "application/json", "Ocp-Apim-Subscription-Key": os.environ["AI_KEY"]}
    curl = get_curl()
    r = await curl.fetch(
        url_concat(
            "https://melanieai.cognitiveservices.azure.com/computervision/imageanalysis:segment",
            {"api-version": "2023-02-01-preview", "mode": "backgroundRemoval"},
        ),
        headers=headers,
        body=orjson.dumps({"url": url}),
        method="POST",
    )
    return r.body


@router.get(
    "/segment_bg",
    name="Remove backgrounds from image",
    description="Performs AI based image segmentation that is more powerful than traditional rembg tooling.",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
)
async def segment_bg(url: HttpUrl, request: Request):
    async with services.verify_token(request), asyncio.timeout(40):
        data = await segment_image_url(url)
        mime = guess_mime(data)
        return Response(data, media_type=mime)
