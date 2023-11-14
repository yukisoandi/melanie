from __future__ import annotations

from typing import Any

from melanie import BaseModel, Field

from .admin import router as AdminRouter
from .ai import router as AiRouter
from .discord import router as DiscordRouter
from .discord_auth import router as DiscordAuthRouter
from .instagram import router as InstagramRouter
from .onlyfans import router as OnlyfansRouter
from .pinterest import router as PinterestRouter
from .roblox import router as RobloxRouter
from .snapchat import router as SnapRouter
from .speech import router as SpeechRoute
from .tiktok import router as TiktokRouter
from .twitter import router as TwitterRouter
from .valorant import router as ValorantRouter
from .web import router as WebRouter
from .webhooks.stripe import router as StripeRouter

_all_routes = [
    OnlyfansRouter,
    AiRouter,
    DiscordRouter,
    InstagramRouter,
    PinterestRouter,
    RobloxRouter,
    SnapRouter,
    SpeechRoute,
    TiktokRouter,
    TwitterRouter,
    ValorantRouter,
    WebRouter,
    DiscordAuthRouter,
    StripeRouter,
    AdminRouter,
]


class GitVersionModel(BaseModel):
    major: int | None = Field(None, alias="Major")
    minor: int | None = Field(None, alias="Minor")
    patch: int | None = Field(None, alias="Patch")
    pre_release_tag: str | None = Field(None, alias="PreReleaseTag")
    pre_release_tag_with_dash: str | None = Field(None, alias="PreReleaseTagWithDash")
    pre_release_label: str | None = Field(None, alias="PreReleaseLabel")
    pre_release_label_with_dash: str | None = Field(None, alias="PreReleaseLabelWithDash")
    pre_release_number: Any | None = Field(None, alias="PreReleaseNumber")
    weighted_pre_release_number: int | None = Field(None, alias="WeightedPreReleaseNumber")
    build_meta_data: int | None = Field(None, alias="BuildMetaData")
    build_meta_data_padded: str | None = Field(None, alias="BuildMetaDataPadded")
    full_build_meta_data: str | None = Field(None, alias="FullBuildMetaData")
    major_minor_patch: str | None = Field(None, alias="MajorMinorPatch")
    sem_ver: str | None = Field(None, alias="SemVer")
    legacy_sem_ver: str | None = Field(None, alias="LegacySemVer")
    legacy_sem_ver_padded: str | None = Field(None, alias="LegacySemVerPadded")
    assembly_sem_ver: str | None = Field(None, alias="AssemblySemVer")
    assembly_sem_file_ver: str | None = Field(None, alias="AssemblySemFileVer")
    full_sem_ver: str | None = Field(None, alias="FullSemVer")
    informational_version: str | None = Field(None, alias="InformationalVersion")
    branch_name: str | None = Field(None, alias="BranchName")
    escaped_branch_name: str | None = Field(None, alias="EscapedBranchName")
    sha: str | None = Field(None, alias="Sha")
    short_sha: str | None = Field(None, alias="ShortSha")
    nu_get_version_v2: str | None = Field(None, alias="NuGetVersionV2")
    nu_get_version: str | None = Field(None, alias="NuGetVersion")
    nu_get_pre_release_tag_v2: str | None = Field(None, alias="NuGetPreReleaseTagV2")
    nu_get_pre_release_tag: str | None = Field(None, alias="NuGetPreReleaseTag")
    version_source_sha: str | None = Field(None, alias="VersionSourceSha")
    commits_since_version_source: int | None = Field(None, alias="CommitsSinceVersionSource")
    commits_since_version_source_padded: str | None = Field(None, alias="CommitsSinceVersionSourcePadded")
    uncommitted_changes: int | None = Field(None, alias="UncommittedChanges")
    commit_date: str | None = Field(None, alias="CommitDate")
