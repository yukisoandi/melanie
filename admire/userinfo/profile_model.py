from __future__ import annotations

from typing import Optional

from melanie import BaseModel as _BaseModel


class BaseModel(_BaseModel, extra="allow"):
    pass


class DiscordUser(BaseModel):
    id: Optional[str]
    username: Optional[str]
    avatar: Optional[str]
    avatar_decoration: Optional[str]
    discriminator: Optional[str]
    public_flags: Optional[int]
    flags: Optional[int]
    banner: Optional[str]
    banner_color: Optional[str]
    accent_color: Optional[int]
    bio: Optional[str]


class ConnectedAccount(BaseModel):
    type: Optional[str]
    id: Optional[str]
    name: Optional[str]
    verified: Optional[bool]


class MutualGuild(BaseModel):
    id: Optional[str]
    nick: Optional[str]


class User1(BaseModel):
    id: Optional[str]
    username: Optional[str]
    avatar: Optional[str]
    avatar_decoration: Optional[str]
    discriminator: Optional[str]
    public_flags: Optional[int]


class GuildMember(BaseModel):
    flags: Optional[int]
    is_pending: Optional[bool]
    joined_at: Optional[str]
    nick: Optional[str]
    pending: Optional[bool]
    premium_since: Optional[str]
    roles: Optional[list[str]]
    user: Optional[User1]
    bio: Optional[str]
    banner: Optional[str]
    mute: Optional[bool]
    deaf: Optional[bool]


class UserProfile(BaseModel):
    bio: Optional[str]
    accent_color: Optional[int]
    banner: Optional[str]


class GuildMemberProfile(BaseModel):
    guild_id: Optional[str]
    bio: Optional[str]
    banner: Optional[str]
    accent_color: Optional[str]


class ProfileModel(BaseModel):
    user: Optional[DiscordUser]
    connected_accounts: Optional[list[ConnectedAccount]]
    premium_since: Optional[str]
    premium_type: Optional[int]
    premium_guild_since: Optional[str]
    profile_themes_experiment_bucket: Optional[int]
    guild_member: Optional[GuildMember]
    user_profile: Optional[UserProfile]
    guild_member_profile: Optional[GuildMemberProfile]
