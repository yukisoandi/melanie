from __future__ import annotations

from typing import Any

from loguru import logger as log

from melanie import BaseModel, Field


class BioLink(BaseModel):
    title: str | None
    lynx_url: str | None
    url: str | None
    link_type: str | None


class BiographyWithEntities(BaseModel):
    raw_text: str | None
    entities: list | None


class ClipsMusicAttributionInfo(BaseModel):
    artist_name: str | None
    song_name: str | None
    uses_original_audio: bool | None
    should_mute_audio: bool | None
    should_mute_audio_reason: str | None
    audio_id: str | None


class CoauthorProducer(BaseModel):
    id: int | None
    is_verified: bool | None
    profile_pic_url: str | None
    username: str | None


class DashInfo(BaseModel):
    is_dash_eligible: bool | None
    video_dash_manifest: str | None
    number_of_qualities: int | None


class Dimensions(BaseModel):
    height: int | None
    width: int | None


class EdgeFollowClass(BaseModel):
    count: int | None


class FluffyNode(BaseModel):
    text: str | None


class NodeUser(BaseModel):
    full_name: str | None
    followed_by_viewer: bool | None
    id: str | None
    is_verified: bool | None
    profile_pic_url: str | None
    username: str | None


class Location(BaseModel):
    id: int | None
    has_public_page: bool | None
    name: str | None
    slug: str | None


class Owner(BaseModel):
    id: str | None
    username: str | None


class SharingFrictionInfo(BaseModel):
    should_have_sharing_friction: bool | None
    bloks_app_url: Any | None


class ThumbnailResource(BaseModel):
    src: str | None
    config_width: int | None
    config_height: int | None


class PageInfo(BaseModel):
    has_next_page: bool | None
    end_cursor: str | None


class StickyNode(BaseModel):
    username: str | None


class EdgeMediaToCaptionEdge(BaseModel):
    node: FluffyNode | None


class TentacledNode(BaseModel):
    user: NodeUser | None
    x: float | None
    y: float | None


class EdgeMutualFollowedByEdge(BaseModel):
    node: StickyNode | None


class EdgeMediaToCaption(BaseModel):
    edges: list[EdgeMediaToCaptionEdge] | None


class EdgeMediaToTaggedUserEdge(BaseModel):
    node: TentacledNode | None


class EdgeMutualFollowedBy(BaseModel):
    count: int | None
    edges: list[EdgeMutualFollowedByEdge] | None


class EdgeMediaToTaggedUser(BaseModel):
    edges: list[EdgeMediaToTaggedUserEdge] | None


class PurpleNode(BaseModel):
    field__typename: str | None = Field(None, alias="__typename")
    id: str | None
    shortcode: str | None
    dimensions: Dimensions | None
    display_url: str | None
    edge_media_to_tagged_user: EdgeMediaToTaggedUser | None
    fact_check_overall_rating: Any | None
    fact_check_information: Any | None
    gating_info: Any | None
    sharing_friction_info: SharingFrictionInfo | None
    media_overlay_info: Any | None
    media_preview: str | None
    owner: Owner | None
    is_video: bool | None
    has_upcoming_event: bool | None
    accessibility_caption: str | None
    dash_info: DashInfo | None
    has_audio: bool | None
    tracking_token: str | None
    video_url: str | None
    video_view_count: int | None
    edge_media_to_caption: EdgeMediaToCaption | None
    edge_media_to_comment: EdgeFollowClass | None
    comments_disabled: bool | None
    taken_at_timestamp: int | None
    edge_liked_by: EdgeFollowClass | None
    edge_media_preview_like: EdgeFollowClass | None
    location: Location | None
    nft_asset_info: Any | None
    thumbnail_src: str | None
    thumbnail_resources: list[ThumbnailResource] | None
    felix_profile_grid_crop: Any | None
    coauthor_producers: list[CoauthorProducer] | None
    pinned_for_users: list[CoauthorProducer] | None
    viewer_can_reshare: bool | None
    encoding_status: Any | None
    is_published: bool | None
    product_type: str | None
    title: str | None
    video_duration: float | None
    clips_music_attribution_info: ClipsMusicAttributionInfo | None


class EdgeFelixVideoTimelineEdge(BaseModel):
    node: PurpleNode | None


class EdgeFelixVideoTimelineClass(BaseModel):
    count: int | None
    page_info: PageInfo | None
    edges: list[EdgeFelixVideoTimelineEdge] | None


class InstagramProfileModel(BaseModel):
    biography: str | None
    bio_links: list[BioLink] | None
    biography_with_entities: BiographyWithEntities | None
    blocked_by_viewer: bool | None
    restricted_by_viewer: bool | None
    country_block: bool | None
    external_url: str | None
    external_url_linkshimmed: str | None
    edge_followed_by: EdgeFollowClass | None
    fbid: str | None
    followed_by_viewer: bool | None
    edge_follow: EdgeFollowClass | None
    follows_viewer: bool | None
    full_name: str | None
    group_metadata: Any | None
    has_ar_effects: bool | None
    has_clips: bool | None
    has_guides: bool | None
    has_channel: bool | None
    has_blocked_viewer: bool | None
    highlight_reel_count: int | None
    has_requested_viewer: bool | None
    hide_like_and_view_counts: bool | None
    id: int | None
    is_business_account: bool | None
    is_professional_account: bool | None
    is_supervision_enabled: bool | None
    is_guardian_of_viewer: bool | None
    is_supervised_by_viewer: bool | None
    is_supervised_user: bool | None
    is_embeds_disabled: bool | None
    is_joined_recently: bool | None
    guardian_id: Any | None
    business_address_json: Any | None
    business_contact_method: str | None
    business_email: Any | None
    business_phone_number: Any | None
    business_category_name: Any | None
    overall_category_name: Any | None
    category_enum: Any | None
    category_name: str | None
    is_private: bool | None
    is_verified: bool | None
    edge_mutual_followed_by: EdgeMutualFollowedBy | None
    profile_pic_url: str | None
    profile_pic_url_hd: str | None
    requested_by_viewer: bool | None
    should_show_category: bool | None
    should_show_public_contacts: bool | None
    show_account_transparency_details: bool | None
    transparency_label: Any | None
    transparency_product: str | None
    username: str | None
    connected_fb_page: Any | None
    pronouns: list | None
    edge_felix_video_timeline: EdgeFelixVideoTimelineClass | None
    edge_owner_to_timeline_media: EdgeFelixVideoTimelineClass | None
    edge_saved_media: EdgeFelixVideoTimelineClass | None
    edge_media_collections: EdgeFelixVideoTimelineClass | None
    followed_by_count: int | None
    following_count: int | None
    post_count: int | None

    @classmethod
    async def from_web_info_response(cls, data: dict):
        user = data["data"]["user"]

        cls = cls.parse_obj(user)
        try:
            cls.followed_by_count = user["edge_followed_by"]["count"]
        except KeyError:
            log.error("Unable to extract edge_followed_by")
        try:
            cls.following_count = user["edge_follow"]["count"]
        except KeyError:
            log.error("Unable to extract edge_follow")
        try:
            cls.post_count = user["edge_owner_to_timeline_media"]["count"]
        except KeyError:
            log.error("Unable to get post count..")
        return cls


class Data(BaseModel):
    user: InstagramProfileModel | None


class TopLevel(BaseModel):
    data: Data | None
    status: str | None


def shortcode_to_mediaid(shortcode) -> int:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    mediaid = 0
    for letter in shortcode:
        mediaid = (mediaid * 64) + alphabet.index(letter)
    return mediaid


class UserPostItem(BaseModel):
    id: str | None
    shortcode: str | None
    url: str | None
    is_video: bool | None
    taken_at_timestamp: int | None
    title: str | None


class InstagramProfileModelResponse(BaseModel):
    avatar_filename: str | None
    avatar_url: str | None
    bio_links: list[Any] | None
    biography: str | None
    external_url: str | None
    followed_by_count: int | None
    following_count: int | None
    full_name: str | None
    has_channel: bool | None
    has_clips: bool | None
    highlight_reel_count: int | None
    id: str | None
    is_business_account: bool | None
    is_joined_recently: bool | None
    is_private: bool | None
    is_professional_account: bool | None
    is_verified: bool | None
    post_count: int | None
    pronouns: list[str] | None
    username: str | None
    post_items: list[UserPostItem] = []
    created_at: float | None
