from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import hashids
import msgpack
import yt_dlp
from humanize import intcomma
from humanize import intword as _intword
from loguru import logger as log
from yt_dlp import YoutubeDL

from melanie import BaseModel, Field, log
from melanie.models.colors import ColorPalette

from .yt import TiktokYoutubeDLResult

_hashids = hashids.Hashids()

TIKTOK_HEADERS = {
    "User-Agent": "5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1",
}
TEMP_VIDEO_META_PATH = Path("/cache/tiktok_meta/")
try:
    GLOBAL_YT_CLIENTS  # type: ignore
except NameError:
    GLOBAL_YT_CLIENTS = {}


class DownloadLogger:
    def debug(self, msg) -> None:
        log.opt(depth=1).debug(msg)

    def warning(self, msg) -> None:
        log.opt(depth=1).warning(msg)

    def error(self, msg) -> None:
        log.opt(depth=1).error(msg)


YDL_OPTS = {"restrictfilenames": True, "trim_file_name": 35, "retries": 0, "logger": DownloadLogger(), "simulate": True}


def get_yt_dl() -> YoutubeDL:
    ident = "1"
    if ident not in GLOBAL_YT_CLIENTS:
        GLOBAL_YT_CLIENTS[ident] = yt_dlp.YoutubeDL(YDL_OPTS)
    return GLOBAL_YT_CLIENTS[ident]


def intword(val: int) -> str:
    if not val:
        val = 0
    if val < 10000:
        return intcomma(val)
    a = _intword(val)
    a = a.replace("thousand", "k")
    return a


class AwemeRequestArgs(BaseModel, allow_population_by_field_name=True):
    video_id: int = Field(..., alias="aweme_id")
    region: str | None = "US"
    timezone_name: str | None = "Etc/GMT"
    locale: str | None = "en"
    app_type: str | None = "normal"
    resolution: str | None = "1080*1920"
    aid: str | None = "1180"
    app_name: str | None = "musical_ly"
    _rticket: int | None = round(time.time_ns() / 1000000)
    device_platform: str | None = "android"
    version_code: str | None = "100000"
    dpi: str | None = "441"
    cpu_support64: bool | None = False
    sys_region: str | None = "US"
    timezone_offset: int | None = 0
    pass_route: str | None = Field(1, alias="pass-route")
    device_brand: str | None = "google"
    os_version: str | None = "8.0.0"
    op_region: str | None = "US"
    app_language: str | None = "en"
    pass_region: str | None = Field(1, alias="pass-region")
    language: str | None = "en"
    channel: str | None = "googleplay"


class ShareInfo(BaseModel, allow_population_by_field_name=True):
    share_title: str | None
    bool_persist: int | None
    share_title_myself: str | None
    share_signature_desc: str | None
    share_signature_url: str | None
    share_quote: str | None
    share_desc_info: str | None
    share_url: str | None
    share_weibo_desc: str | None
    share_desc: str | None
    share_title_other: str | None
    share_link_desc: str | None


class Avatar300x300(BaseModel, allow_population_by_field_name=True):
    height: int | None
    uri: str | None
    url_list: list[str] | None
    width: int | None


class AvatarMedium(BaseModel, allow_population_by_field_name=True):
    uri: str | None
    url_list: list[str] | None
    width: int | None
    height: int | None


class ShareQrcodeUrl(BaseModel, allow_population_by_field_name=True):
    url_list: list | None
    width: int | None
    height: int | None
    uri: str | None


class ShareInfo1(BaseModel, allow_population_by_field_name=True):
    share_desc: str | None
    share_title: str | None

    share_title_myself: str | None
    share_title_other: str | None
    share_desc_info: str | None
    share_url: str | None
    share_weibo_desc: str | None


class CoverUrlItem(Avatar300x300):
    pass


class VideoIcon(BaseModel, allow_population_by_field_name=True):
    uri: str | None
    url_list: list | None
    width: int | None
    height: int | None


class Avatar168x168(Avatar300x300):
    pass


class AvatarThumb(AvatarMedium):
    pass


class AvatarLarger(AvatarMedium):
    pass


class Author(BaseModel, allow_population_by_field_name=True):
    status: int | None
    uniqueId: str | None = Field(None, alias="unique_id")
    avatar_300x300: Avatar300x300 | None
    has_youtube_token: bool | None
    authority_status: int | None
    shield_comment_notice: int | None
    with_commerce_entry: bool | None
    is_discipline_member: bool | None
    unique_id_modify_time: int | None
    user_tags: Any | None
    advance_feature_item_order: Any | None
    user_mode: int | None
    aweme_count: int | None
    region: str | None
    prevent_download: bool | None
    is_phone_binded: bool | None
    accept_private_policy: bool | None
    enterprise_verify_reason: str | None
    youtube_channel_id: str | None
    bind_phone: str | None
    has_twitter_token: bool | None
    fb_expire_time: int | None
    room_id: int | None
    live_verify: int | None
    shield_digg_notice: int | None
    avatar_medium: AvatarMedium | None
    shield_follow_notice: int | None
    ins_id: str | None
    download_setting: int | None
    item_list: Any | None
    live_agreement: int | None
    download_prompt_ts: int | None
    share_info: ShareInfo1 | None
    user_canceled: bool | None
    cover_url: list[CoverUrlItem] | None
    comment_filter_status: int | None
    need_points: Any | None
    has_email: bool | None
    cv_level: str | None
    type_label: Any | None
    homepage_bottom_toast: Any | None
    bold_fields: Any | None
    mutual_relation_avatars: Any | None
    video_icon: VideoIcon | None
    create_time: int | None
    uid: str | None
    nickname: str | None
    follower_count: int | None
    verify_info: str | None
    followers_detail: Any | None
    account_region: str | None
    avatar_168x168: Avatar168x168 | None
    total_favorited: int | None
    hide_search: bool | None
    commerce_user_level: int | None
    platform_sync_info: Any | None
    google_account: str | None
    youtube_channel_title: str | None
    custom_verify: str | None
    is_ad_fake: bool | None
    follower_status: int | None
    live_commerce: bool | None
    is_star: bool | None
    relative_users: Any | None
    avatar_thumb: str | None
    is_block: bool | None
    show_image_bubble: bool | None
    twitter_id: str | None
    comment_setting: int | None
    react_setting: int | None
    signature: str | None
    following_count: int | None
    tw_expire_time: int | None
    has_orders: bool | None
    cha_list: Any | None
    search_highlight: Any | None
    avatar_larger: AvatarLarger | None
    need_recommend: int | None
    duet_setting: int | None
    share_qrcode_uri: str | None
    user_period: int | None
    user_rate: int | None
    ad_cover_url: Any | None
    short_id: str | None
    special_lock: int | None
    has_facebook_token: bool | None
    with_shop_entry: bool | None
    secret: int | None
    apple_account: int | None
    can_set_geofencing: Any | None
    white_cover_url: Any | None
    youtube_expire_time: int | None
    verification_type: int | None
    geofencing: Any | None
    twitter_name: str | None
    language: str | None
    has_insights: bool | None
    follow_status: int | None
    favoriting_count: int | None
    avatar_uri: str | None
    stitch_setting: int | None
    events: Any | None

    @property
    def embed_icon(self):
        return next(x for x in self.avatar_medium.url_list if True)


class GroupIdList(BaseModel, allow_population_by_field_name=True):
    groupd_id_list0: Any | None = Field(None, alias="GroupdIdList0")
    groupd_id_list1: list[int] | None = Field(None, alias="GroupdIdList1")


class LabelTop(AvatarMedium):
    pass


class DownloadAddr(BaseModel, allow_population_by_field_name=True):
    height: int | None
    data_size: int | None
    uri: str | None
    url_list: list[str] | None
    width: int | None


class AiDynamicCover(Avatar300x300):
    pass


class AiDynamicCoverBak(AvatarMedium):
    pass


class PlayAddr(BaseModel, allow_population_by_field_name=True):
    height: int | None
    url_key: str | None
    data_size: int | None
    file_hash: str | None
    file_cs: str | None
    uri: str | None
    url_list: list[str] | None
    width: int | None


class DynamicCover(AvatarMedium):
    pass


class OriginCover(BaseModel, allow_population_by_field_name=True):
    url_list: list[str] | None
    width: int | None
    height: int | None
    uri: str | None


class PlayAddr1(BaseModel, allow_population_by_field_name=True):
    data_size: int | None
    file_hash: str | None
    file_cs: str | None
    uri: str | None
    url_list: list[str] | None
    width: int | None
    height: int | None
    url_key: str | None


class BitRateItem(BaseModel, allow_population_by_field_name=True):
    gear_name: str | None
    quality_type: int | None
    bit_rate: int | None
    play_addr: PlayAddr1 | None
    is_h265: int | None
    is_bytevc1: int | None
    dub_infos: Any | None


class Cover(AvatarMedium):
    pass


class Video(BaseModel):
    has_watermark: bool | None
    bytes: bytes | None
    download_addr: DownloadAddr | None
    is_callback: bool | None
    big_thumbs: Any | None
    is_bytevc1: int | None
    ai_dynamic_cover: AiDynamicCover | None
    ai_dynamic_cover_bak: AiDynamicCoverBak | None
    ratio: str | None
    height: int | None
    need_set_token: bool | None
    tags: Any | None
    play_addr: PlayAddr | None
    width: int | None
    dynamic_cover: DynamicCover | None
    origin_cover: OriginCover | None
    bit_rate: list[BitRateItem] | None
    duration: int | None
    is_h265: int | None
    cdn_url_expired: int | None
    cover: Cover | None


class RiskInfos(BaseModel, allow_population_by_field_name=True):
    type: int | None
    content: str | None
    vote: bool | None
    warn: bool | None
    risk_sink: bool | None


class Statistics(BaseModel, allow_population_by_field_name=True):
    digg_count: int | None
    play_count: int | None
    share_count: int | None
    aweme_id: str | None
    comment_count: int | None
    lose_count: int | None
    lose_comment_count: int | None
    whatsapp_share_count: int | None
    collect_count: int | None
    download_count: int | None
    forward_count: int | None


class CommerceInfo(BaseModel, allow_population_by_field_name=True):
    auction_ad_invited: bool | None
    with_comment_filter_words: bool | None
    adv_promotable: bool | None


class ReviewResult(BaseModel, allow_population_by_field_name=True):
    review_status: int | None


class Status(BaseModel, allow_population_by_field_name=True):
    is_delete: bool | None
    allow_comment: bool | None
    private_status: int | None
    reviewed: int | None
    is_prohibited: bool | None
    review_result: ReviewResult | None
    aweme_id: str | None
    allow_share: bool | None
    in_reviewing: bool | None
    self_see: bool | None
    download_status: int | None


class CoverMedium(AvatarMedium):
    pass


class StrongBeatUrl(OriginCover):
    pass


class AvatarThumb1(Avatar300x300):
    pass


class AvatarMedium1(OriginCover):
    pass


class CoverMedium1(BaseModel, allow_population_by_field_name=True):
    width: int | None
    height: int | None
    uri: str | None
    url_list: list[str] | None


class ChorusInfo(BaseModel, allow_population_by_field_name=True):
    start_ms: int | None
    duration_ms: int | None


class MatchedSong(BaseModel, allow_population_by_field_name=True):
    id: str | None
    author: str | None
    title: str | None
    h5_url: str | None
    cover_medium: CoverMedium1 | None
    performers: Any | None
    chorus_info: ChorusInfo | None


class CoverLarge(AvatarMedium):
    pass


class PlayUrl(OriginCover):
    pass


class CoverThumb(AvatarMedium):
    pass


class MatchedPgcSound(BaseModel, allow_population_by_field_name=True):
    title: str | None
    mixed_title: str | None
    mixed_author: str | None
    author: str | None


class Music(BaseModel, allow_population_by_field_name=True):
    user_count: int | None
    external_song_info: list | None
    is_author_artist: bool | None
    lyric_short_position: Any | None
    cover_medium: CoverMedium | None
    extra: str | None
    status: int | None
    owner_handle: str | None
    shoot_duration: int | None
    is_original: bool | None
    mid: str | None
    artists: list | None
    source_platform: int | None
    duration: int | None
    position: Any | None
    author_position: Any | None
    strong_beat_url: StrongBeatUrl | None
    id_str: str | None
    album: str | None
    collect_stat: int | None
    owner_id: str | None
    owner_nickname: str | None
    avatar_medium: str | None
    tag_list: Any | None
    matched_song: MatchedSong | None
    search_highlight: Any | None
    author: str | None
    cover_large: CoverLarge | None
    play_url: PlayUrl | None
    audition_duration: int | None
    video_duration: int | None
    is_pgc: bool | None
    is_matched_metadata: bool | None
    is_audio_url_with_cookie: bool | None
    offline_desc: str | None
    binded_challenge_id: int | None
    prevent_download: bool | None
    preview_end_time: int | None
    is_original_sound: bool | None
    multi_bit_rate_play_info: Any | None
    is_commerce_music: bool | None
    mute_share: bool | None
    dmv_auto_show: bool | None
    id: int | None
    title: str | None
    cover_thumb: CoverThumb | None
    author_deleted: bool | None
    preview_start_time: int | None
    matched_pgc_sound: MatchedPgcSound | None


class VideoControl(BaseModel, allow_population_by_field_name=True):
    allow_duet: bool | None
    allow_download: bool | None
    show_progress_bar: int | None
    draft_progress_bar: int | None
    allow_dynamic_wallpaper: bool | None
    timer_status: int | None
    allow_music: bool | None
    allow_stitch: bool | None
    share_type: int | None
    allow_react: bool | None
    prevent_download_type: int | None


class Extra(BaseModel, allow_population_by_field_name=True):
    fatal_item_ids: list | None
    logid: str | None
    now: int | None


class LogPb(BaseModel, allow_population_by_field_name=True):
    impr_id: str | None


class Thumbnail1(AiDynamicCoverBak):
    pass


class DisplayImage1(AiDynamicCoverBak):
    pass


class OwnerWatermarkImage1(AiDynamicCoverBak):
    pass


class UserWatermarkImage1(AvatarMedium):
    pass


class Thumbnail1(AiDynamicCoverBak):
    pass


class UserWatermarkImage(AiDynamicCoverBak):
    pass


class OwnerWatermarkImage(OriginCover):
    pass


class Thumbnail(OriginCover):
    pass


class DisplayImage(OriginCover):
    pass


class OwnerWatermarkImage(OriginCover):
    pass


class Image(BaseModel):
    thumbnail: Thumbnail | None
    display_image: DisplayImage | None


class ImagePostCover(BaseModel):
    thumbnail: Thumbnail1 | None
    display_image: DisplayImage1 | None


class ImagePostInfo(BaseModel):
    music_volume: float | None
    images: list[Image] | None
    image_post_cover: ImagePostCover | None


class TikTokVideo(BaseModel, extra="allow"):
    id: int | None = Field(None, alias="aweme_id")
    group_id: str | None
    color_lookup: ColorPalette | None
    request_user_agent: str | None
    image_post_info: ImagePostInfo | None
    item_comment_settings: int | None
    desc_language: str | None
    geofencing_regions: Any | None
    search_highlight: Any | None
    geofencing: Any | None
    anchors: Any | None
    playlist_blocked: bool | None
    question_list: Any | None
    share_info: ShareInfo | None
    long_video: Any | None
    without_watermark: bool | None
    distribute_type: int | None
    green_screen_materials: Any | None
    content_desc: str | None
    position: Any | None
    is_pgcshow: bool | None
    disable_search_trending_bar: bool | None
    music_begin_time_in_ms: int | None
    create_time: int | None
    author: Author | None
    video_labels: list | None
    sort_label: str | None
    video_text: list | None
    label_top_text: Any | None
    commerce_config_data: Any | None
    item_stitch: int | None
    group_id_list: GroupIdList | None
    share_url: str | None
    label_top: LabelTop | None
    nickname_position: Any | None
    misc_info: str | None
    video: Video | None
    risk_infos: RiskInfos | None
    is_relieve: bool | None
    interaction_stickers: Any | None
    origin_comment_ids: Any | None
    mask_infos: list | None
    distance: str | None
    author_user_id: int | None
    region: str | None
    user_digged: int | None
    text_extra: list | None
    bodydance_score: int | None
    collect_stat: int | None
    prevent_download: bool | None
    statistics: Statistics | None = Field(None, alias="stats")
    uniqid_position: Any | None
    with_promotional_music: bool | None
    challenge_position: Any | None
    item_react: int | None
    commerce_info: CommerceInfo | None
    follow_up_publish_from_id: int | None
    is_ads: bool | None
    cmt_swt: bool | None
    item_duet: int | None
    is_preview: int | None
    cha_list: Any | None
    aweme_type: int | None
    desc: str = ""
    rate: int | None
    have_dashboard: bool | None
    status: Status | None
    is_top: int | None
    is_vr: bool | None
    hybrid_label: Any | None
    products_info: Any | None
    music: Music | None
    image_infos: Any | None
    is_hash_tag: int | None
    video_control: VideoControl | None
    cover_labels: Any | None
    need_trim_step: bool | None
    content_desc_extra: list | None
    music_end_time_in_ms: int | None
    video_bytes: bytes | None = Field(None, repr=False)
    video_filename: str | None
    yt_info: TiktokYoutubeDLResult | None

    @property
    def images(self) -> list[str]:
        return [i.display_image.url_list[-1] for i in self.image_post_info.images] if self.image_post_info else None

    @property
    def hashid(self) -> str:
        return _hashids.encode(int(self.id))

    @property
    def share_url(self) -> str:
        return f"https://www.tiktok.com/@{self.author.uniqueId}/video/{self.id}"

    @property
    def hd_url(self):
        urls = (x for x in self.video.play_addr.url_list if "tiktokv" in x)
        return next(urls)

    def to_bytes(self):
        return msgpack.packb(self.dict())

    @classmethod
    def from_bytes(cls, data: bytes) -> TikTokVideo:
        if not isinstance(data, (memoryview, bytes, bytearray)):
            msg = "Must unpack from bytes"
            raise ValueError(msg)

        return cls(**msgpack.unpackb(data))
