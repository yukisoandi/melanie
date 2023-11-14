from __future__ import annotations

from melanie import BaseModel


class Emoji(BaseModel):
    animated: bool | None
    available: bool | None
    id: str | None
    managed: bool | None
    name: str | None
    require_colons: bool | None
    roles: list | None


class Tags(BaseModel):
    bot_id: str | None


class Role(BaseModel):
    color: int | None
    flags: int | None
    hoist: bool | None
    id: str | None
    managed: bool | None
    mentionable: bool | None
    name: str | None
    permissions: str | None
    position: int | None
    tags: Tags | None
    icon: str | None
    unicode_emoji: str | None


class Sticker(BaseModel):
    asset: str | None
    available: bool | None
    description: str | None
    format_type: int | None
    guild_id: str | None
    id: str | None
    name: str | None
    tags: str | None
    type: int | None


class Guild(BaseModel):
    afk_timeout: int | None
    banner: str | None
    default_message_notifications: int | None
    explicit_content_filter: int | None
    features: list[str] | None
    icon: str | None
    id: str | None
    max_members: int | None
    max_stage_video_channel_users: int | None
    max_video_channel_users: int | None
    mfa_level: int | None
    name: str | None
    nsfw: bool | None
    nsfw_level: int | None
    owner_id: str | None
    preferred_locale: str | None
    premium_progress_bar_enabled: bool | None
    premium_subscription_count: int | None
    premium_tier: int | None
    public_updates_channel_id: str | None
    region: str | None
    rules_channel_id: str | None
    splash: str | None
    system_channel_flags: int | None
    system_channel_id: str | None
    vanity_url_code: str | None
    verification_level: int | None
    widget_enabled: bool | None


class TokenData(BaseModel):
    access_token: str | None
    expires_in: int | None
    guild: Guild | None
    refresh_token: str | None
    scope: str | None
    token_type: str | None


class UserGuild(BaseModel):
    features: list[str] | None
    icon: str | None
    id: str | None
    name: str | None
    owner: bool | None
    permissions: str | None


class Identify(BaseModel):
    accent_color: int | None
    avatar: str | None
    banner_color: str | None
    discriminator: str | None
    email: str | None
    flags: int | None
    id: str | None
    locale: str | None
    mfa_enabled: bool | None
    premium_type: int | None
    public_flags: int | None
    username: str | None
    verified: bool | None


class UserData(BaseModel):
    guilds: list[UserGuild] | None
    identify: Identify | None


class DiscordJoinOauthModel(BaseModel):
    bot_name: str | None

    what_the_heck_is_this: str = "Testing out the ability for us to receive data on who invites the bot to a server to make the process of whitelisting and managing servers easier. You can read more about this here --> https://discord.com/developers/docs/topics/oauth2#advanced-bot-authorization. Prettier page coming soon....."
    audit_key: str | None
    guild_added_in: str | None
    permissions_granted: str | None
    token_data: TokenData | None
    user_data: UserData | None
