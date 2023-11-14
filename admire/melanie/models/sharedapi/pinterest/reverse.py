from __future__ import annotations

from typing import Annotated, List, Optional

from pydantic import AnyUrl, Field

from melanie import BaseModel as _BL


class BaseModel(_BL, extra="allow"):
    pass


class AdData(BaseModel):
    grid_click_type: Optional[int]


class AggregatedStats(BaseModel):
    saves: Optional[int]
    done: Optional[int]


class Field200X(BaseModel):
    url: Optional[AnyUrl]
    width: Optional[int]


class NativeCreator(BaseModel):
    explicitly_followed_by_me: Optional[bool]
    full_name: Optional[str]
    image_small_url: Optional[AnyUrl]
    native_pin_count: Optional[int]
    show_creator_profile: Optional[bool]
    id: Optional[str]
    first_name: Optional[str]
    username: Optional[str]


class Embed(BaseModel):
    src: Optional[AnyUrl]
    height: Optional[int]
    width: Optional[int]
    type: Optional[str]


class ImageCrop(BaseModel):
    min_y: Optional[int]
    max_y: Optional[int]


class Field345X(BaseModel):
    width: Optional[int]
    height: Optional[int]
    url: Optional[AnyUrl]


class ReactionCounts(BaseModel):
    field_1: Annotated[Optional[int], Field(alias="1")]


class RichSummary(BaseModel):
    id: Optional[str]
    products: Optional[List]
    actions: Optional[List]
    display_name: Optional[str]
    site_name: Optional[str]
    type_name: Optional[str]


class Metadata(BaseModel):
    version: Optional[str]
    root_pin_id: Optional[str]
    is_compatible: Optional[bool]
    compatible_version: Optional[str]
    canvas_aspect_ratio: Optional[float]
    pin_image_signature: Optional[str]
    is_editable: Optional[bool]
    is_promotable: Optional[bool]
    pin_title: Optional[str]
    root_user_id: Optional[str]


class BlockStyle(BaseModel):
    corner_radius: Optional[int]
    width: Optional[int]
    rotation: Optional[int]
    y_coord: Optional[int]
    x_coord: Optional[int]
    height: Optional[int]


class ImageImages(BaseModel):
    originals: Optional[Field345X]
    field_1200x: Annotated[Optional[Field345X], Field(alias="1200x")]
    field_345x: Annotated[Optional[Field345X], Field(alias="345x")]
    field_736x: Annotated[Optional[Field345X], Field(alias="736x")]


class Style(BaseModel):
    background_color: Optional[str]


class VHLS(BaseModel):
    url: Optional[AnyUrl]
    width: Optional[int]
    height: Optional[int]
    duration: Optional[int]
    thumbnail: Optional[AnyUrl]


class AggregatedPinData(BaseModel):
    id: Optional[str]
    has_xy_tags: Optional[bool]
    is_dynamic_collections: Optional[bool]
    aggregated_stats: Optional[AggregatedStats]
    is_shop_the_look: Optional[bool]
    catalog_collection_type: Optional[int]


class CoverImages(BaseModel):
    field_200x: Annotated[Optional[Field200X], Field(alias="200x")]


class DatumImages(BaseModel):
    field_345x: Annotated[Optional[Field345X], Field(alias="345x")]
    field_736x: Annotated[Optional[Field345X], Field(alias="736x")]


class Image(BaseModel):
    images: Optional[ImageImages]
    dominant_color: Optional[str]


class VideoList(BaseModel):
    v_hlsv3_mobile: Annotated[Optional[VHLS], Field(alias="V_HLSV3_MOBILE")]
    v_hls_hevc: Annotated[Optional[VHLS], Field(alias="V_HLS_HEVC")]


class Board(BaseModel):
    followed_by_me: Optional[bool]
    privacy: Optional[str]
    cover_images: Optional[CoverImages]
    image_thumbnail_url: Optional[AnyUrl]
    name: Optional[str]
    id: Optional[str]
    layout: Optional[str]
    owner: Optional[NativeCreator]
    is_ads_only: Optional[bool]
    image_cover_url: Optional[AnyUrl]


class Block(BaseModel):
    block_type: Optional[int]
    text: Optional[str]
    block_style: Optional[BlockStyle]
    type: Optional[str]
    image_signature: Optional[str]
    image: Optional[Image]


class Videos(BaseModel):
    video_list: Optional[VideoList]
    id: Optional[str]


class PagesPreview(BaseModel):
    music_attributions: Optional[List]
    style: Optional[Style]
    type: Optional[str]
    id: Optional[str]
    image_adjusted: Optional[Image]
    layout: Optional[int]
    should_mute: Optional[bool]
    image_signature: Optional[str]
    blocks: Optional[List[Block]]
    image: Optional[Image]
    image_signature_adjusted: Optional[str]


class StoryPinData(BaseModel):
    pages_preview: Optional[List[PagesPreview]]
    metadata: Optional[Metadata]
    type: Optional[str]
    id: Optional[str]
    total_video_duration: Optional[int]
    static_page_count: Optional[int]
    has_affiliate_products: Optional[bool]
    has_product_pins: Optional[bool]
    has_virtual_try_on_makeup_pins: Optional[bool]
    page_count: Optional[int]


class PinterestReverseData(BaseModel):
    dominant_color: Optional[str]
    is_unsafe: Optional[bool]
    is_full_width: Optional[bool]
    image_square_url: Optional[AnyUrl]
    share_count: Optional[int]
    done_by_me: Optional[bool]
    images: Optional[DatumImages]
    is_cpc_ad: Optional[bool]
    is_whitelisted_for_tried_it: Optional[bool]
    comment_reply_comment_id: Optional[str]
    image_medium_url: Optional[AnyUrl]
    promoted_is_removable: Optional[bool]
    is_eligible_for_pdp_plus: Optional[bool]
    aggregated_pin_data: Optional[AggregatedPinData]
    image_signature: Optional[str]
    title: Optional[str]
    view_tags: Optional[List]
    created_at: Optional[str]
    reaction_counts: Optional[ReactionCounts]
    is_repin: Optional[bool]
    additional_hide_reasons: Optional[List]
    is_year_in_preview: Optional[bool]
    destination_url_type: Optional[int]
    is_shopping_ad: Optional[bool]
    is_native: Optional[bool]
    image_crop: Optional[ImageCrop]
    domain: Optional[str]
    is_eligible_for_brand_catalog: Optional[bool]
    top_interest: Optional[int]
    board: Optional[Board]
    tracking_params: Optional[str]
    is_eligible_for_pdp: Optional[bool]
    pinner: Optional[NativeCreator]
    is_unsafe_for_comments: Optional[bool]
    native_creator: Optional[NativeCreator]
    shopping_flags: Optional[List]
    category: Optional[str]
    is_eligible_for_aggregated_comments: Optional[bool]
    is_scene: Optional[bool]
    comment_count: Optional[int]
    is_promoted: Optional[bool]
    question_comment_id: Optional[str]
    is_ghost: Optional[bool]
    cacheable_id: Optional[str]
    is_downstream_promotion: Optional[bool]
    ip_eligible_for_stela: Optional[bool]
    should_open_in_stream: Optional[bool]
    ad_match_reason: Optional[int]
    description: Optional[str]
    promoted_is_quiz: Optional[bool]
    id: Optional[str]
    promoted_is_showcase: Optional[bool]
    repin_count: Optional[int]
    promoted_is_max_video: Optional[bool]
    ad_data: Optional[AdData]
    type: Optional[str]
    is_eligible_for_web_closeup: Optional[bool]
    should_preload: Optional[bool]
    virtual_try_on_type: Optional[int]
    is_premiere: Optional[bool]
    is_video: Optional[bool]
    comments_disabled: Optional[bool]
    music_attributions: Optional[List]
    is_stale_product: Optional[bool]
    is_eligible_for_related_products: Optional[bool]
    promoted_is_lead_ad: Optional[bool]
    is_oos_product: Optional[bool]
    creative_types: Optional[List[str]]
    grid_title: Optional[str]
    rich_summary: Optional[RichSummary]
    link: Optional[AnyUrl]
    tracked_link: Optional[AnyUrl]
    embed: Optional[Embed]
    origin_pinner: Optional[NativeCreator]
    story_pin_data: Optional[StoryPinData]
    should_mute: Optional[bool]
    videos: Optional[Videos]


class PinterestReverseResult(BaseModel):
    code: Optional[int]
    additional_metadata: Optional[str]
    search_identifier: Optional[str]
    endpoint_name: Optional[str]
    data: Optional[List[PinterestReverseData]]
    bookmark: Optional[str]
    status: Optional[str]
    is_single_prominent_object: Optional[bool]
    url: Optional[str]
    message: Optional[str]
