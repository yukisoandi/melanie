import subprocess
from typing import Optional

from distributed import Event

from melanie import BaseModel
from runtimeopt import offloaded


class User(BaseModel):
    """A new type describing a User."""

    name: str
    groups: set[str] = set()
    email: Optional[str] = None


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
    format: Optional[Format]

    @property
    def video_stream(self):
        return next((x for x in self.streams if x.codec_type == "video"), None)

    @property
    def audio_stream(self):
        return next((x for x in self.streams if x.codec_type == "audio"), None)

    @property
    def data_stream(self):
        return next((x for x in self.streams if x.codec_type == "data"), None)

    def is_h264(self):
        return self.video_stream.codec_name == "h264"


def get_video_encoding(url) -> FFProbeResultModel:
    result = subprocess.check_output(
        ["ffprobe", str(url), "-loglevel", "error", "-hide_banner", "-print_format", "json", "-show_streams", "-show_format"],
        timeout=15,
    )
    return FFProbeResultModel.parse_raw(result)


def make_h264_video(url=None, timeout: int = 30):
    from pathlib import Path

    from distributed import Lock
    from xxhash import xxh3_64_hexdigest

    from melanie import borrow_temp_file_sync, worker_download

    root = Path("/tmp/h264cache")
    root.mkdir(exist_ok=True)
    data = worker_download(url)
    key = xxh3_64_hexdigest(data)
    with Lock(name=f"h264conv:{key}"), borrow_temp_file_sync(extension=".mp4") as infile:
        resultfile = root / key
        resultfile = resultfile.with_suffix(".mp4")
        if not resultfile.exists():
            infile.write_bytes(data)
            cmd_call = [
                "ffmpeg",
                "-i",
                str(infile),
                "-c:v",
                "libopenh264",
                "-c:a",
                "libfdk_aac",
                "-filter:v",
                "scale='min(1920,iw)':min'(1080,ih)',fps=30",
                "-allow_skip_frames",
                "1",
                "-profile:v",
                "main",
                "-movflags",
                "+faststart",
                "-y",
                str(resultfile),
            ]
            subprocess.check_output(cmd_call, timeout=timeout)

        return resultfile.read_bytes()


@offloaded
def run_video_convpipeline(url: str, event: Event, force: bool = False, timeout: int = 30):
    from loguru import logger as log

    from melanie import capturetime

    test_result = get_video_encoding(url)
    if not test_result.is_h264() or force:
        event.set()
        log.warning(f"Beginning conversion to H264 for {url}  ")
        with capturetime(f"H264 Encoding {url}"):
            return make_h264_video(url, timeout)
