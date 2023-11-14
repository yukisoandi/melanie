from __future__ import annotations

import asyncio
from contextlib import suppress

from anyio import Path as AsyncPath
from loguru import logger as log
from xxhash import xxh32_hexdigest
from yarl import URL

from melanie import BaseModel, global_curl, log
from melanie.curl import S3Curl, get_curl
from melanie.models import Field


class GifRenderJobResult(BaseModel):
    url: str
    size: int
    path: str = Field(..., repr=False)


CACHE_ROOT = AsyncPath("/tmp/gifcache")


async def convert_to_gif(
    input_path_or_url: str,
    quality: int = 70,
    height: int = 500,
    fps: int = 20,
    speed: int = 1,
    job_timeout: int = 20,
    force: bool = False,
) -> bytes:
    input_path_or_url = str(input_path_or_url)
    _url = URL(input_path_or_url)
    await CACHE_ROOT.mkdir(exist_ok=True, parents=True)
    if not _url.scheme:
        in_file = AsyncPath(input_path_or_url)
    else:
        key = f"gifd{xxh32_hexdigest(str(_url))}"
        in_file = CACHE_ROOT / f"{key}.mp4"
        log.warning("Downloading {}", input_path_or_url)
        curl = global_curl()
        r = await curl.fetch(str(_url))
        await in_file.write_bytes(r.body)
    abs_input = await in_file.absolute()
    arg_key = xxh32_hexdigest(f"Q{quality}H{height}FPS{fps}SPD{speed}")
    out_file = CACHE_ROOT / f"{in_file.stem}_{arg_key}.gif"
    abs_input2 = abs_input.with_stem(f"{abs_input.stem}_tmp")
    abs_output = await out_file.absolute()
    try:
        key = f"gifconv/{out_file.name}"
        out_url = f"https://volatile.hurt.af/{key}"
        curl = get_curl()
        r = await curl.fetch(out_url, raise_error=False)
        if r.code == 200 and not force:
            log.success("{} has already been rendered", out_file)
            data = r.body
            return GifRenderJobResult(path=str(await out_file.absolute()), url=out_url, size=len(data))
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *["-i", str(abs_input), "-t", "12", "-c:v", "copy", "-c:a", "copy", str(abs_input2)],
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "gifski",
            *["--output", str(abs_output), "-r", str(fps), "--quality", str(quality), "-H", str(height), "--lossy-quality", str(quality), str(abs_input2)],
        )
        try:
            async with asyncio.timeout(job_timeout):
                await proc.communicate()
        finally:
            with suppress(ProcessLookupError):
                proc.kill()
        data = await out_file.read_bytes()
        await S3Curl.put_object("volatile", key, data, "image/gif")
        return GifRenderJobResult(path=str(abs_output), url=out_url, size=len(data))
    finally:
        await abs_input2.unlink(missing_ok=True)
        await abs_input.unlink(missing_ok=True)
        await abs_output.unlink(missing_ok=True)
