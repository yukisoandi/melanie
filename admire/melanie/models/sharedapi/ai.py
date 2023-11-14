from __future__ import annotations

import hashlib
import time
from base64 import b64encode

import anyascii

from melanie import BaseModel, Field


class AdvancedInfo(BaseModel):
    key: str | None
    value: int | None


class ImageEvaluationResult(BaseModel):
    adult: bool | None = Field(None, description="Indicates if an image is classified as adult.")
    racy: bool | None = Field(None, description="Indicates if the image is classified as racy.")
    adult_classification_score: float | None = Field(None, description="Probability image is adult.")
    racy_classification_score: float | None = Field(None, description="Probability image is racy.")
    advanced_info: list[AdvancedInfo] | None


class ChatModelResponse(BaseModel):
    processing_time: float | None
    beam_texts: list[list[float | str]] | None
    episode_done: bool | None
    id: str | None
    text: str | None


class SearchResultItem(BaseModel):
    title: str | None
    content: str | None
    url: str | None


class SearchResult(BaseModel):
    response: list[SearchResultItem] = []


class ChatSearchRequest(BaseModel):
    q: str
    n: int


class ChatSessionStartResponse(BaseModel):
    session_id: str
    created_at: int = time.time()


class ChatRequestMessage(BaseModel):
    message: str
    session_id: str


class ChatTalkResponse(BaseModel):
    message: str


class Error(BaseModel):
    code: str | None
    message: str | None


class AzureOCRError(BaseModel):
    error: Error | None


class ModelType(BaseModel):
    u2net: str = "u2net"
    u2netp: str = "u2netp"
    u2net_human_seg: str = "u2net_human_seg"
    u2net_cloth_seg: str = "u2net_cloth_seg"


class AIImageGenerationResponse(BaseModel):
    url: str = Field(..., description="URL of the image generated. Image should be immediately avaliable.")
    idea: str


def make_ai_url(security_key, path, expire_timeframe: int = 3600, base_url: str = "", filtered_ip: str = "") -> str:
    security_key = anyascii.anyascii(security_key)
    path = anyascii.anyascii(path)
    expire_timestamp = int(time.time()) + expire_timeframe
    token_content = f"{security_key}{path}{expire_timestamp}{filtered_ip}"
    md5sum = hashlib.md5()
    md5sum.update(token_content.encode("ascii"))
    token_digest = md5sum.digest()
    token_base64 = b64encode(token_digest).decode("ascii")
    token_formatted = token_base64.replace("\n", "").replace("+", "-").replace("/", "_").replace("=", "")
    return f"{base_url}{path}?token={token_formatted}&expires={expire_timestamp}"
