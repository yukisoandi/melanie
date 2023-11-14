from __future__ import annotations

import io
from typing import NamedTuple

from melanie.models.base import BaseModel

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"}


class Dominant(NamedTuple):
    decimal: int


class ColorPalette(BaseModel):
    colors: list[tuple[int, int, int]]

    def hex(self, idx: int = 0) -> str:
        return rgb_to_hex(*self.colors[idx])

    @property
    def html(self) -> str:
        return self.hex(0)

    @property
    def decimal(self) -> int:
        return rgb_to_int(self.colors[0])

    @property
    def dominant(self) -> Dominant:
        return Dominant(self.decimal)


ColorLookup = ColorPalette


def rgb_to_hex(r, g, b) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_to_int(rgb: tuple[int]) -> int:
    decimal = rgb[0]
    decimal = (decimal << 8) + rgb[1]
    return (decimal << 8) + rgb[2]


def calculate_score(rgb):
    R, G, B = rgb
    return (0.212 * R + 0.701 * G + 0.087 * B) / 255


def drop_outliars(_p):
    valid = []
    for c in _p:
        score = calculate_score(c)
        if score > 0.14:
            valid.append(c)
    if not valid:
        valid.extend(_p)
    return valid


async def curl_download_url(url: str):
    from melanie import log
    from melanie.curl import CurlError, get_curl

    curl = get_curl()

    try:
        r = await curl.fetch(
            url,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        )
    except CurlError:
        log.exception("Curl download error")
        return None

    return bytes(r.body)


def build_palettes_4(url_or_bytes, ncolors: int = 10) -> bytes:
    import filetype
    import orjson
    from loguru import logger as log
    from PIL import Image, ImagePalette

    mime = filetype.guess_mime(url_or_bytes)
    if mime and "image" in mime:
        try:
            buf = io.BytesIO(url_or_bytes)
            with Image.open(buf) as img:
                if hasattr(img, "n_frames") and img.n_frames > 2:
                    img.seek(img.n_frames // 2)
                opt_img = img.quantize(ncolors, method=Image.LIBIMAGEQUANT)
                p = ImagePalette.ImagePalette(palette=opt_img.getpalette())
                c = list(p.colors.keys())
                valid = drop_outliars(c)
                return orjson.dumps(valid)
        except Exception as e:
            log.warning("Uhandled error when trying to build the pallelte: {}", str(e))
