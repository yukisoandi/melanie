from __future__ import annotations

from datetime import datetime  # noqa

from melanie import BaseModel, Field


class About(BaseModel):
    read_link: str | None = Field(None, alias="readLink")
    name: str | None


class PurpleContractualRule(BaseModel):
    _type: str | None
    text: str | None


class PurpleThumbnail(BaseModel):
    content_url: str | None = Field(None, alias="contentUrl")
    width: int | None
    height: int | None


class Mention(BaseModel):
    name: str | None


class FluffyThumbnail(BaseModel):
    content_url: str | None = Field(None, alias="contentUrl")


class VideoThumbnail(BaseModel):
    width: int | None
    height: int | None


class QueryContext(BaseModel):
    original_query: str | None = Field(None, alias="originalQuery")


class ItemValue(BaseModel):
    id: str | None


class DeepLink(BaseModel):
    name: str | None
    url: str | None


class ValueImage(BaseModel):
    content_url: str | None = Field(None, alias="contentUrl")
    thumbnail: PurpleThumbnail | None


class ProviderImage(BaseModel):
    thumbnail: FluffyThumbnail | None


class Video(BaseModel):
    name: str | None
    thumbnail_url: str | None = Field(None, alias="thumbnailUrl")
    thumbnail: VideoThumbnail | None
    motion_thumbnail_url: str | None = Field(None, alias="motionThumbnailUrl")


class Item(BaseModel):
    answer_type: str | None = Field(None, alias="answerType")
    result_index: int | None = Field(None, alias="resultIndex")
    value: ItemValue | None


class FluffyContractualRule(BaseModel):
    _type: str | None
    target_property_name: str | None = Field(None, alias="targetPropertyName")
    target_property_index: int | None = Field(None, alias="targetPropertyIndex")
    must_be_close_to_content: bool | None = Field(None, alias="mustBeCloseToContent")
    license: DeepLink | None
    license_notice: str | None = Field(None, alias="licenseNotice")


class Provider(BaseModel):
    _type: str | None
    name: str | None
    image: ProviderImage | None


class Mainline(BaseModel):
    items: list[Item] | None


class WebPagesValue(BaseModel):
    id: str | None
    contractual_rules: list[FluffyContractualRule] | None = Field(None, alias="contractualRules")
    name: str | None
    url: str | None
    is_family_friendly: bool | None = Field(None, alias="isFamilyFriendly")
    display_url: str | None = Field(None, alias="displayUrl")
    snippet: str | None
    date_last_crawled: datetime | None = Field(None, alias="dateLastCrawled")
    language: str | None
    is_navigational: bool | None = Field(None, alias="isNavigational")
    deep_links: list[DeepLink] | None = Field(None, alias="deepLinks")


class NewsValue(BaseModel):
    contractual_rules: list[PurpleContractualRule] | None = Field(None, alias="contractualRules")
    name: str | None
    url: str | None
    image: ValueImage | None
    description: str | None
    about: list[About] | None
    provider: list[Provider] | None
    date_published: datetime | None = Field(None, alias="datePublished")
    video: Video | None
    category: str | None
    mentions: list[Mention] | None


class RankingResponse(BaseModel):
    mainline: Mainline | None


class WebPages(BaseModel):
    web_search_url: str | None = Field(None, alias="webSearchUrl")
    total_estimated_matches: int | None = Field(None, alias="totalEstimatedMatches")
    value: list[WebPagesValue] | None


class News(BaseModel):
    id: str | None
    read_link: str | None = Field(None, alias="readLink")
    value: list[NewsValue] | None


class RawSearchResponse(BaseModel):
    _type: str | None
    query_context: QueryContext | None = Field(None, alias="queryContext")
    web_pages: WebPages | None = Field(None, alias="webPages")
    news: News | None
    ranking_response: RankingResponse | None = Field(None, alias="rankingResponse")


class SearchItem(BaseModel):
    title: str | None
    content: str | None
    url: str | None
    docid: str | None


class SearchResult(BaseModel):
    response: list[SearchItem] = []
