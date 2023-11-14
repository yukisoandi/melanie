from __future__ import annotations

from discord.http import Route

from melanie import BaseModel

# Error messages

TIME_OUT = "The request timed out or we are being ratelimited, please try again after a few moments."
INVOKE_ERROR = "Something went wrong while adding the emoji(s). Has the limit been reached?"
HTTP_EXCEPTION = "Something went wrong while adding the emoji(s): the source file may be too big or the limit may have been reached."
FILE_SIZE = "Unfortunately, it seems the attachment was too large to be sent."
SAME_SERVER_ONLY = "I can only edit emojis from this server!"
ROLE_HIERARCHY = "I cannot perform this action due to the Discord role hierarchy!"


class ImageToolarge(Exception):
    pass


class CreateGuildSticker(BaseModel):
    name: str
    description: str
    tags: str


class EditGuildSticker(BaseModel):
    name: str
    tags: str
    description: str = None


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"
