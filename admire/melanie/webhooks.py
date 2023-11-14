from __future__ import annotations

from typing import NamedTuple

MELANIE_PFP = "https://cdn.discordapp.com/avatars/877732605242511412/994fa958e86513f6315b3df8b305b169.png?size=1024"


# Melanie


class EmbedField(NamedTuple):
    name: str
    value: str
    inline: bool = True


class Avatar(NamedTuple):
    name: str = "melanie"
    icon_url: str = MELANIE_PFP


class Footer(NamedTuple):
    text: str
    icon_url: str = None


class WebhookResponse(NamedTuple):
    status: int
    content: str = None


# def send_hook(url: str, title: str, desc: str, fields: list[NamedTuple] = None, footer: Footer = None, username: str = "melanie", color="03b2f8", avatar: Avatar = Avatar()) -> WebhookResponse:

#     if footer:
#     for field in fields:
#         if not isinstance(field, EmbedField):
#     if footer:
