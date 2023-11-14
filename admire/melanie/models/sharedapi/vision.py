from __future__ import annotations

from datetime import date, datetime  # noqa

import xxhash

from melanie import BaseModel, Field
from runtimeopt.offloaded import offloaded


@offloaded
def prepare_image(input_image: str) -> tuple[bytes, str]:
    BG_URL = "https://cdn.discordapp.com/attachments/928400431137296425/1076698854650564710/white-background-500x500.jpg"
    from filetype import guess_mime
    from wand.image import Image

    from melanie import worker_download
    from melanie.redis import blocking_redis

    with Image(blob=input_image) as _image:
        _image: Image
        bg_img: Image
        if _image.width > 50 and _image.height > 50:
            return input_image, xxhash.xxh64_hexdigest(input_image)
        with blocking_redis() as redis:
            _bg = redis.get("preparebaseimg")
            if not _bg:
                _bg = worker_download(BG_URL)
                assert "image" in guess_mime(_bg)
                redis.set("preparebaseimg", _bg)
            with Image(blob=_bg) as bg_img:
                bg_img.composite(_image, left=100, top=100)
                data = bg_img.make_blob("png")
                return data, xxhash.xxh64_hexdigest(data)


class Style(BaseModel):
    name: str | None
    confidence: float | None


class Word(BaseModel):
    bounding_box: list[int] | None
    text: str | None
    confidence: float | None


class Appearance(BaseModel):
    style: Style | None


class Line(BaseModel):
    bounding_box: list[int] | None
    text: str | None
    appearance: Appearance | None
    words: list[Word] | None


class ReadResult(BaseModel):
    page: int | None
    angle: float | None
    width: int | None
    height: int | None
    unit: str | None
    language: str | None
    lines: list[Line] | None


class AnalyzeResult(BaseModel):
    version: str | None
    model_version: date | None
    read_results: list[ReadResult] | None


class AzureOcrReadRaw(BaseModel):
    status: str | None
    created_date_time: datetime | None
    last_updated_date_time: datetime | None
    analyze_result: AnalyzeResult | None


class OCRRquest(BaseModel):
    url: str = Field(..., description="URL of the image to be read. PNG or JPEG supported. Must be larger than 50x50")


class OCRReadResponse(BaseModel):
    display_text: str | None
    lines: list[str] | None = []
