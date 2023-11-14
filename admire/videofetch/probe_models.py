from __future__ import annotations

from typing import Optional

from melanie import BaseModel
from melanie.models import Field


class Disposition(BaseModel):
    default: Optional[int]
    dub: Optional[int]
    original: Optional[int]
    comment: Optional[int]
    lyrics: Optional[int]
    karaoke: Optional[int]
    forced: Optional[int]
    hearing_impaired: Optional[int]
    visual_impaired: Optional[int]
    clean_effects: Optional[int]
    attached_pic: Optional[int]
    timed_thumbnails: Optional[int]
    captions: Optional[int]
    descriptions: Optional[int]
    metadata: Optional[int]
    dependent: Optional[int]
    still_image: Optional[int]


class Tags(BaseModel):
    language: Optional[str]
    handler_name: Optional[str]
    vendor_id: Optional[str]
    encoder: Optional[str]
    major_brand: Optional[str]
    minor_version: Optional[str]
    compatible_brands: Optional[str]


class Stream(BaseModel):
    index: Optional[int]
    codec_name: Optional[str]
    codec_long_name: Optional[str]
    profile: Optional[str]
    codec_type: Optional[str]
    codec_tag_string: Optional[str]
    codec_tag: Optional[str]
    width: Optional[int]
    height: Optional[int]
    coded_width: Optional[int]
    coded_height: Optional[int]
    closed_captions: Optional[int]
    film_grain: Optional[int]
    has_b_frames: Optional[int]
    sample_aspect_ratio: Optional[str]
    display_aspect_ratio: Optional[str]
    pix_fmt: Optional[str]
    level: Optional[int]
    chroma_location: Optional[str]
    field_order: Optional[str]
    refs: Optional[int]
    is_avc: Optional[str]
    nal_length_size: Optional[str]
    id: Optional[str]
    r_frame_rate: Optional[str]
    avg_frame_rate: Optional[str]
    time_base: Optional[str]
    start_pts: Optional[int]
    start_time: Optional[str]
    duration_ts: Optional[int]
    duration: Optional[str]
    bit_rate: Optional[str]
    bits_per_raw_sample: Optional[str]
    nb_frames: Optional[str]
    extradata_size: Optional[int]
    disposition: Optional[Disposition]
    tags: Optional[Tags]
    sample_fmt: Optional[str]
    sample_rate: Optional[str]
    channels: Optional[int]
    channel_layout: Optional[str]
    bits_per_sample: Optional[int]
    initial_padding: Optional[int]


class Format(BaseModel):
    filename: Optional[str]
    nb_streams: Optional[int]
    nb_programs: Optional[int]
    format_name: Optional[str]
    format_long_name: Optional[str]
    start_time: Optional[str]
    duration: Optional[str]
    size: Optional[str]
    bit_rate: Optional[str]
    probe_score: Optional[int]
    tags: Optional[Tags]


class FFProbeResultModel(BaseModel):
    streams: Optional[list[Stream]]
    format_data: Optional[Format] = Field(None, alias="format")

    @property
    def video_stream(self):
        return next(filter(self.streams, lambda x: x.codec_type == "video"), None)

    @property
    def audio_stream(self):
        return next(filter(self.streams, lambda x: x.codec_type == "audio"), None)

    @property
    def data_stream(self):
        return next(filter(self.streams, lambda x: x.codec_type == "data"), None)
