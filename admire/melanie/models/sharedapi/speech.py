from __future__ import annotations

from melanie import BaseModel


class STTResult(BaseModel):
    status: str | None
    display_text_raw: str | None
    display_text: str | None = ""
    offset: str | None
    duration: str | None
    source_language: str | None = "english"


class Segment(BaseModel):
    id: int | None
    seek: int | None
    start: float | None
    end: float | None
    text: str | None
    tokens: list[int] | None
    temperature: float | None
    avg_logprob: float | None
    compression_ratio: float | None
    no_speech_prob: float | None


class OpenAITranslationResult(BaseModel):
    task: str | None
    language: str | None
    duration: float | None
    text: str | None
    segments: list[Segment] | None
