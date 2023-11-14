from __future__ import annotations

from typing import Any, Optional

from discord import File
from discord.http import HTTPClient
from filetype.filetype import guess_mime

from melanie.curl import worker_download
from runtimeopt import offloaded

from .constants import CreateGuildSticker, EditGuildSticker, ImageToolarge, V9Route


@offloaded
def generate_sticker_from_url(img_url: str, svg: bool = False) -> bytes:
    import cairosvg
    from wand.image import Image

    img_bytes = worker_download(img_url)
    if svg:
        kwargs = {"parent_width": 320, "parent_height": 320}
        return cairosvg.svg2png(bytestring=img_bytes, **kwargs)

    with Image(blob=img_bytes) as i:
        i.coalesce()
        i.optimize_layers()
        i.compression_quality = 100
        png_bytes = i.make_blob(format="apng" if i.animation else "png")
    if len(png_bytes) > 500000 - 1:
        msg = f"Image too large even after compression attempts. Size is {len(png_bytes) / 1000}kb. Needs to be less than 499kb \U0001f97a "
        raise ImageToolarge(msg)

    return png_bytes


def modify_guild_sticker(http_client: HTTPClient, guild_id: int, sticker_id: int, payload: EditGuildSticker, reason: Optional[str]) -> Any:
    return http_client.request(
        V9Route("PATCH", "/guilds/{guild_id}/stickers/{sticker_id}", guild_id=guild_id, sticker_id=sticker_id),
        json=payload.dict(),
        reason=reason,
    )


@offloaded
def convert_img_format(img_url, format):
    from wand.image import Image

    img_bytes = worker_download(img_url)
    with Image(blob=img_bytes) as i:
        output = i.make_blob(format=format)
    return output


def create_guild_sticker(http_client: HTTPClient, guild_id: int, payload: CreateGuildSticker, file: File, reason: str) -> Any:
    initial_bytes = file.fp.read(16)
    try:
        mime_type = guess_mime(initial_bytes) or ("application/json" if initial_bytes.startswith(b"{") else "application/octet-stream")
    finally:
        file.reset()

    form: list[dict[str, Any]] = [{"name": "file", "value": file.fp, "filename": file.filename, "content_type": mime_type}]
    form.extend({"name": k, "value": v} for k, v in payload.dict().items())
    route = V9Route("POST", "/guilds/{guild_id}/stickers", guild_id=guild_id)
    return http_client.request(route, form=form, files=[file])
