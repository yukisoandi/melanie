import asyncio
import io
import os
import string
import textwrap
from contextlib import suppress

import httpx
import orjson
from async_lru import alru_cache
from fastapi.responses import UJSONResponse
from filetype import guess_extension
from melanie import borrow_temp_file, get_curl, log, snake_cased_dict
from melanie.models.sharedapi.speech import OpenAITranslationResult, STTResult
from melanie.models.sharedapi.tts import STTJob, TTSFormatOptions, TTSResult, TTSTranslationRequest
from pydantic import AnyHttpUrl
from tornado.httputil import url_concat
from unidecode import unidecode
from xxhash import xxh32_hexdigest

from api_services import api_username_var, services
from core import media_url_from_request
from routes._base import APIRouter, Query, Request

REMOVE_PUNC = str(string.punctuation)
for i in [",", ".", "-", "_", "!", ":", "?"]:
    REMOVE_PUNC = REMOVE_PUNC.replace(i, "")

STT_ARGS = {"language": "en-US", "format": "simple", "profanity": "raw"}
STT_ROUTE = "https://eastus.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
STT_HEADERS: dict[str, str] = {
    "Content-type": "audio/ogg; codecs=opus",
    "Accept": "application/json",
    "User-Agent": "melaniebot",
    "Ocp-Apim-Subscription-Key": os.environ["SPEECH_KEY"],
}
STT_URI = url_concat(STT_ROUTE, STT_ARGS)

ffmpeg_sem = asyncio.BoundedSemaphore(4)
router = APIRouter(prefix="/api/speech")


async def process_voice_clip(t: STTJob) -> bytes:
    async with asyncio.timeout(30), ffmpeg_sem:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *["-i", str(t.url), "-vn", "-c:a", "libopus", "-t", str(t.limit), "-b:a", "64K", "-f", "ogg", "-"],
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

        try:
            out, _ = await proc.communicate()
            return out
        finally:
            with suppress(ProcessLookupError):
                proc.kill()


@alru_cache(ttl=300)
async def transcribe(url: str):
    url = str(url)

    t = STTJob(url=url, limit=58, headers=STT_HEADERS, endpoint=STT_URI)
    # if "cdn.discordapp.com" in url and "voice-message.ogg" in url:

    async with services.htx.stream("GET", url) as r1:
        r1.raise_for_status()
        async with services.htx.stream("POST", t.endpoint, headers=t.headers, content=r1.aiter_bytes()) as r2:
            r2.raise_for_status()
            result = await r2.aread()
            data = orjson.loads(result)
            data = snake_cased_dict(data)
            data["status"] = data["recognition_status"]
            return STTResult.parse_obj(data)


@alru_cache(ttl=300)
async def do_openai_translation(url: str) -> STTResult:
    curl = get_curl()

    r = await curl.fetch(url)
    headers = {"Authorization": "Bearer " + os.getenv("OPENAI_API_KEY", "")}
    buf = io.BytesIO(r.body)
    buf.name = f"file.{guess_extension(r.body)}"
    r = await services.htx.post(
        "https://api.openai.com/v1/audio/translations",
        headers=headers,
        files={"file": buf},
        data={"model": "whisper-1", "response_format": "verbose_json"},
    )
    try:
        r.raise_for_status()
    except httpx.HTTPError:
        log.exception("Http Error {}", r.json())
        raise

    result = OpenAITranslationResult.parse_raw(r.content)
    return STTResult(status="Okay", display_text=result.text, source_language=result.language)


async def transcribe_post_processor(result: STTResult):
    text_value = str(result.display_text)
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.getenv("OPENAI_API_KEY", ""),
    }

    json_data = {
        "model": "gpt-3.5-turbo-16k",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant for the company ZyntriQix. Your task is to correct any spelling discrepancies in the transcribed text. Only add necessary punctuation such as periods, commas, and capitalization, and use only the context provided.",
            },
            {
                "role": "user",
                "content": text_value,
            },
        ],
    }

    curl = get_curl()
    r = await curl.fetch("https://api.openai.com/v1/chat/completions", headers=headers, method="POST", body=orjson.dumps(json_data))
    data = orjson.loads(r.body)
    msg = data["choices"][-1]["message"]["content"]
    result.display_text_raw = text_value
    result.display_text = str(msg)


@router.get(
    "/stt",
    name="Perform Speech to Text",
    tags=["speech"],
    description="Return the text content of an audio file",
    response_model=STTResult,
    operation_id="getSst",
)
async def make_transcribe(
    request: Request,
    url: AnyHttpUrl = Query(..., description="URL of the audio or video file."),
    translate: bool = Query(False, description="Perform the operation with smart translation"),
):
    async with services.verify_token(request, f"STT: {url}"):
        url = str(url)
        async with services.locks[url], asyncio.timeout(90):
            return await do_openai_translation(url) if translate else await transcribe(url)


async def set_tags(data: bytes, artist: str, title: str, output_format: str) -> bytes:
    async with ffmpeg_sem, borrow_temp_file(extension=f".{output_format}") as inputfile, borrow_temp_file(extension=f".{output_format}") as outputfile:
        await inputfile.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *[
                "-loglevel",
                "error",
                "-hide_banner",
                "-i",
                str(inputfile),
                "-metadata",
                f"artist={artist}",
                "-metadata",
                f"title={title}",
                "-c:a",
                "copy",
                "-vn",
                str(outputfile),
            ],
        )

        async with asyncio.timeout(5):
            await proc.communicate()
            return await outputfile.read_bytes()


async def make_tts(voice_choice: str, text: str, output_format: str) -> bool | bytes:
    voice_choice = voice_choice.replace("_", "-")
    text = text.replace(">", "")
    text = text.replace("<", "")
    curl = get_curl()
    data = f"<speak version='1.0' xml:lang='en-US'><voice xml:lang='en-US' name='{voice_choice}Neural'> <break /> {text} </voice></speak>"
    headers = {
        "Ocp-Apim-Subscription-Key": os.environ["SPEECH_KEY"],
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3" if output_format == "mp3" else "ogg-24khz-16bit-mono-opus",
        "User-Agent": "melaniebot discord.gg/melaniebot",
    }

    r = await curl.fetch("https://eastus.tts.speech.microsoft.com/cognitiveservices/v1", headers=headers, body=data, method="POST")
    return r.body


@router.post(
    "/tts",
    name="Perform Text to Speech",
    tags=["speech"],
    description="Generate an MP3 of text input",
    response_model=TTSResult,
    operation_id="getTts",
)
async def generate_tts(
    tts_request: TTSTranslationRequest,
    request: Request,
    output_format: TTSFormatOptions = "ogg",
    user_id: str | None = None,
):
    tts_request.text = unidecode(tts_request.text, replace_str="", errors="replace")
    name = f'SpeechSynth_{xxh32_hexdigest(f"{tts_request.text}{tts_request.speaker_name}")}'
    audit = f"tts: {tts_request.text}"
    if user_id:
        audit += f" user_id: {user_id}"
    async with services.verify_token(request, description=audit):
        api_name = api_username_var.get() or "Melanie"
        key = f"{name}.{output_format}"
        if api_name == "Bleed" and await services.redis.ratelimited(f"tts_rl:{user_id}", 3, 120):
            log.error("TTS ratelimit {}", user_id)
            return UJSONResponse(f"User ratelimit for {user_id} reached", 429)
        async with services.locks[key], asyncio.timeout(90):
            cached = await services.get_cached_target(key)
            if not cached:
                tag_name = tts_request.speaker_name.split("-")[-1]
                data = await make_tts(voice_choice=tts_request.speaker_name, text=tts_request.text, output_format=output_format)
                if api_name != "Bleed":
                    data = await set_tags(
                        data,
                        title=f"{tag_name}: {textwrap.shorten(tts_request.text, 100, placeholder='..')}",
                        artist="melanie",
                        output_format=output_format,
                    )
                await services.save_target(key, data, ex=3600)
            return TTSResult(url=media_url_from_request(key, direct=True))
