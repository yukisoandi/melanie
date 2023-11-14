from __future__ import annotations

from melanie import BaseModel

KEY = "fa591c210b0d4bb49607556c4e70631f"


AZ_HEADER: dict[str, str] = {"Ocp-Apim-Subscription-Key": KEY, "Ocp-Apim-Subscription-Region": "centralus", "Content-type": "application/json"}


class DetectLanguageRequest(BaseModel):
    text: str


class DetectLanguageResponse(BaseModel):
    language: str
    score: float


class LanguageTranslationRequest(BaseModel):
    text: str
    to_lang: str
    from_lang: str | None


class LanguageTranslationRespone(BaseModel):
    text: str
    to_lang: str
    from_lang: str | None
    detected_lang: str | None
