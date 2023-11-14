from __future__ import annotations

import textwrap
from typing import Any

import arrow
import discord  # noqa
import regex as re
from boltons.strutils import find_hashtags

from melanie import BaseModel, Field, intword


class InstaPostURLMeta(BaseModel):
    media_id: str
    url: str


class InstagramCredentialItem(BaseModel):
    alias: str
    password: str
    email: str


class Location(BaseModel):
    short_name: str | None
    external_source: str | None
    name: str | None
    address: str | None
    city: str | None
    lng: float | None
    lat: float | None
    is_eligible_for_guides: bool | None


class Thumbnails(BaseModel):
    video_length: float | None
    thumbnail_width: int | None
    thumbnail_height: int | None
    thumbnail_duration: float | None
    sprite_urls: list[str] | None
    thumbnails_per_row: int | None
    total_thumbnail_num_per_sprite: int | None
    max_thumbnails_per_sprite: int | None
    sprite_width: int | None
    sprite_height: int | None
    rendered_width: int | None
    file_size_kb: int | None


class Candidate(BaseModel):
    width: int | None
    height: int | None
    url: str | None


class IgtvFirstFrame(Candidate):
    pass


class FirstFrame(Candidate):
    pass


class AdditionalCandidates(BaseModel):
    igtv_first_frame: IgtvFirstFrame | None
    first_frame: FirstFrame | None


class ImageVersions2(BaseModel):
    candidates: list[Candidate] | None
    additional_candidates: AdditionalCandidates | None


class FriendshipStatus(BaseModel):
    following: bool | None
    outgoing_request: bool | None
    is_bestie: bool | None
    is_restricted: bool | None
    is_feed_favorite: bool | None


class FanClubInfo(BaseModel):
    fan_club_id: Any | None
    fan_club_name: Any | None


class User(BaseModel):
    username: str | None
    full_name: str | None
    is_private: bool | None
    profile_pic_url: str | None
    profile_pic_id: str | None

    is_verified: bool | None


class InstagramUserResponse(BaseModel):
    username: str | None
    full_name: str | None
    is_private: bool | None
    avatar_filename: str | None
    avatar_url: str | None
    is_verified: bool | None


class In(BaseModel):
    user: InstagramUserResponse | None
    position: list[float] | None
    start_time_in_video_in_sec: Any | None
    duration_in_video_in_sec: Any | None


class Usertags(BaseModel):
    in_: list[In] | None = Field(alias="in")


class MashupInfo(BaseModel):
    mashups_allowed: bool | None
    can_toggle_mashups_allowed: bool | None
    has_been_mashed_up: bool | None
    formatted_mashups_count: Any | None
    original_media: Any | None
    non_privacy_filtered_mashups_media_count: Any | None
    mashup_type: Any | None
    is_creator_requesting_mashup: bool | None
    has_nonmimicable_additional_audio: Any | None


class VideoVersion(BaseModel):
    type: int | None
    width: int | None
    height: int | None
    url: str | None
    id: str | None


class User2(InstagramUserResponse):
    pass


class Caption(BaseModel):
    text: str | None


class CommentInformTreatment(BaseModel):
    should_have_inform_treatment: bool | None
    text: str | None
    url: Any | None
    action_type: Any | None


class SharingFrictionInfo(BaseModel):
    should_have_sharing_friction: bool | None
    bloks_app_url: Any | None
    sharing_friction_payload: Any | None


class MusicMetadata(BaseModel):
    music_canonical_id: str | None
    audio_type: Any | None
    music_info: Any | None
    original_sound_info: Any | None
    pinned_media_ids: Any | None


class CarouselMedia(BaseModel):
    id: str | None
    media_type: int | None
    video_versions: list[VideoVersion] | None
    image_versions2: ImageVersions2 | None
    original_width: int | None
    original_height: int | None
    accessibility_caption: str | None
    pk: str | None
    carousel_parent_id: str | None
    usertags: Usertags | None
    commerciality_status: str | None


class InstagramCarouselMediaResponse(BaseModel):
    url: str | None
    preview_image_url: str | None
    preview_image_filename: str | None
    is_video: bool | None = False
    filename: str | None


class Item(BaseModel):
    taken_at: int | None
    pk: str | None
    id: str | None
    device_timestamp: int | None
    media_type: int | None
    code: str | None
    client_cache_key: str | None
    filter_type: int | None
    is_unified_video: bool | None
    location: Location | None
    lat: float | None
    lng: float | None
    should_request_ads: bool | None
    caption_is_edited: bool | None
    like_and_view_counts_disabled: bool | None
    commerciality_status: str | None
    is_paid_partnership: bool | None
    is_visual_reply_commenter_notice_enabled: bool | None
    original_media_has_visual_reply_media: bool | None
    has_delayed_metadata: bool | None
    comment_likes_enabled: bool | None
    comment_threading_enabled: bool | None
    has_more_comments: bool | None
    max_num_visible_preview_comments: int | None
    preview_comments: list | None
    comments: list | None
    can_view_more_preview_comments: bool | None
    comment_count: int | None
    hide_view_all_comment_entrypoint: bool | None
    inline_composer_display_condition: str | None
    title: str | None
    carousel_media_count: int | None
    carousel_media: list[CarouselMedia] | None
    product_type: str | None
    nearly_complete_copyright_match: bool | None
    media_cropping_info: dict[str, Any] | None
    thumbnails: Thumbnails | None
    igtv_exists_in_viewer_series: bool | None
    is_post_live: bool | None
    image_versions2: ImageVersions2 | None
    original_width: int | None
    original_height: int | None
    user: User | None
    can_viewer_reshare: bool | None
    like_count: int | None
    has_liked: bool | None
    top_likers: list | None
    facepile_top_likers: list | None
    photo_of_you: bool | None
    usertags: Usertags | None
    is_organic_product_tagging_eligible: bool | None
    can_see_insights_as_brand: bool | None
    mashup_info: MashupInfo | None
    video_subtitles_confidence: float | None
    video_subtitles_uri: str | None
    is_dash_eligible: int | None
    video_dash_manifest: str | None
    video_codec: str | None
    number_of_qualities: int | None
    video_versions: list[VideoVersion] | None
    has_audio: bool | None
    video_duration: float | None
    view_count: int | None
    caption: Caption | None
    featured_products_cta: Any | None
    comment_inform_treatment: CommentInformTreatment | None
    sharing_friction_info: SharingFrictionInfo | None
    can_viewer_save: bool | None
    is_in_profile_grid: bool | None
    profile_grid_control_enabled: bool | None
    organic_tracking_token: str | None
    has_shared_to_fb: int | None
    deleted_reason: int | None
    integrity_review_decision: str | None
    commerce_integrity_review_decision: Any | None
    music_metadata: MusicMetadata | None
    is_artist_pick: bool | None


class PostModel(BaseModel):
    items: list[Item] | None
    num_results: int | None
    more_available: bool | None
    auto_load_more_enabled: bool | None
    status: str | None


class Comment(BaseModel):
    pk: str | None
    user_id: int | None
    text: str | None
    type: int | None
    created_at: int | None
    created_at_utc: int | None
    content_type: str | None
    status: str | None
    bit_flags: int | None
    did_report_as_spam: bool | None
    share_enabled: bool | None
    user: InstagramUserResponse | None
    is_covered: bool | None
    media_id: str | None
    has_liked_comment: bool | None
    comment_like_count: int | None
    private_reply_status: int | None


class InstagramPostItem(BaseModel):
    id: str | None
    title: str | None
    reply_count: int | None
    taken_at: int | None
    comment_count: int | None
    is_video: bool | None = False
    like_count: int | None
    view_count: int | None
    sidecars: list[InstagramCarouselMediaResponse] = []
    sidecar_count: int | None
    image_url: str | None
    image_filename: str | None
    video_url: str | None
    video_filename: str | None
    video_duration: float | None
    caption: Caption | None
    preview_image_url: str | None
    preview_image_filename: str | None


class InstagramPostResponse(BaseModel):
    num_results: int | None = 0
    share_url: str | None
    author: InstagramUserResponse | None
    items: list[InstagramPostItem] | None = []

    @staticmethod
    def clean_caption(caption: str) -> str:
        caption = " ".join(re.sub("(#[A-Za-z0-9]+)|(@[A-Za-z0-9]+)|([^0-9A-Za-z \t])|(\\w+:\\/\\/\\S+)", " ", caption).split())
        if not caption:
            return ""
        ht = find_hashtags(caption)
        caption = str(caption)

        for h in ht:
            caption = caption.replace(h, "")

        caption = caption.replace("#", "")

        return textwrap.shorten(caption, 800, placeholder="...")

    def make_embed(self) -> discord.Embed:
        import discord

        em = discord.Embed()
        si = self.items[0]
        if si.taken_at:
            em.timestamp = arrow.get(si.taken_at).naive

        if self.author.username and self.author.avatar_url:
            em.set_author(name=self.author.username, icon_url=self.author.avatar_url)

        em.description = ""
        if si.caption and si.caption.text:
            em.description = self.clean_caption(si.caption.text)

        views = f"👀  {intword(si.view_count)}" if si.view_count else ""
        likes = f"❤️  {intword(si.like_count)}" if si.like_count else ""
        cmnts = f"💬  {intword(si.comment_count)}" if si.comment_count else ""
        footer_txt = f"instagram | {likes} {views} {cmnts}"

        from instagram.instagram import IG_ICON_URL

        em.set_footer(text=footer_txt, icon_url=IG_ICON_URL)

        return em


class InstagramPostRequest(BaseModel):
    content: str
    user_id: int
    guild_id: int
