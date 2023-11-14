from __future__ import annotations

from typing import Any

from melanie import BaseModel, Field


class BadgeItem(BaseModel):
    id: int | None
    name: str | None
    description: str | None
    image_url: str | None


class RobloxUserProfileResponse(BaseModel):
    name: str | None
    follower_count: int | None
    following_count: int | None
    display_name: str | None
    description: str | None
    presence: str | None
    has_verified_badge: bool | None
    created: float | None = Field(None, description="UTC timestamp of when account was created")
    is_banned: bool | None
    id: int | None
    last_online: float | None = Field(None, description="UTC timestamp of when user last seen online")
    last_location: str | None
    avatar_url: str | None = Field(None, description="The Roblox user's currently wearing image")
    badges: list[BadgeItem] | None
    previous_names: list[str] = []


class UserSearchResult(BaseModel):
    user_id: int | None = Field(None, alias="UserId")
    name: str | None = Field(None, alias="Name")
    display_name: str | None = Field(None, alias="DisplayName")
    blurb: str | None = Field(None, alias="Blurb")
    previous_user_names_csv: str | None = Field(None, alias="PreviousUserNamesCsv")
    is_online: bool | None = Field(None, alias="IsOnline")
    last_location: Any | None = Field(None, alias="LastLocation")
    user_profile_page_url: str | None = Field(None, alias="UserProfilePageUrl")
    last_seen_date: Any | None = Field(None, alias="LastSeenDate")
    primary_group: str | None = Field(None, alias="PrimaryGroup")
    primary_group_url: str | None = Field(None, alias="PrimaryGroupUrl")
    has_verified_badge: bool | None = Field(None, alias="HasVerifiedBadge")


class UserSearchResponse(BaseModel):
    names: list[str] | None
    keyword: str | None = Field(None, alias="Keyword")
    start_index: int | None = Field(None, alias="StartIndex")
    max_rows: int | None = Field(None, alias="MaxRows")
    total_results: int | None = Field(None, alias="TotalResults")
    user_search_results: list[UserSearchResult] | None = Field(None, alias="UserSearchResults")
