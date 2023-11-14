from datetime import datetime
from typing import Any, Optional

from melanie import BaseModel

# discord.Message.webhook_id


class Author(BaseModel):
    id: Optional[str]
    username: Optional[str]
    global_name: Optional[Any]
    display_name: Optional[Any]
    avatar: Optional[str]
    avatar_decoration: Optional[Any]
    discriminator: Optional[str]
    public_flags: Optional[int]
    bot: Optional[bool]


class Image(BaseModel):
    url: Optional[str]
    proxy_url: Optional[str]
    width: Optional[int]
    height: Optional[int]


class Interaction(BaseModel):
    id: Optional[str]
    type: Optional[int]
    name: Optional[str]
    user: Optional[Author]


class Embed(BaseModel):
    type: Optional[str]
    url: Optional[str]
    title: Optional[str]
    description: Optional[str]
    color: Optional[int]
    image: Optional[Image]


class DiscordAPIMessage(BaseModel, extra="allow"):
    id: Optional[str]
    type: Optional[int]
    content: Optional[str]
    channel_id: Optional[str]
    author: Optional[Author]
    attachments: Optional[list]
    embeds: Optional[list[Embed]]
    mentions: Optional[list]
    mention_roles: Optional[list]
    pinned: Optional[bool]
    mention_everyone: Optional[bool]
    tts: Optional[bool]
    timestamp: Optional[datetime]
    edited_timestamp: Optional[Any]
    flags: Optional[int]
    components: Optional[list]
    application_id: Optional[str]
    interaction: Optional[Interaction]
    webhook_id: Optional[str]

    @classmethod
    async def find(cls, bot, channel_id: int, message_id: int):
        from melanie import get_curl

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Origin": "https://discord.com",
            "Pragma": "no-cache",
            "Referer": "https://discord.com/channels/@me",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "mel",
            "Authorization": f"Bot {bot.http.token}",
        }
        curl = get_curl()
        r = await curl.fetch(f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}", headers=headers)
        return cls.parse_raw(r.body)
