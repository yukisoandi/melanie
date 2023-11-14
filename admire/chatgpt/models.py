from __future__ import annotations

from typing import List, Optional

from melanie import BaseModel


class Message(BaseModel):
    role: str
    content: str


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    id: Optional[str]
    object: Optional[str]
    created: Optional[int]
    choices: Optional[List[Choice]]
    usage: Optional[Usage]
