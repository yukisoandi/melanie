# generated by datamodel-codegen:
#   timestamp: 2022-01-23T06:14:44+00:00


from __future__ import annotations

from typing import Optional

from melanie import BaseModel, Field

# os.getenv


class Emotion(BaseModel):
    angry: Optional[float] = None
    disgust: Optional[float] = None
    fear: Optional[float] = None
    happy: Optional[float] = None
    sad: Optional[float] = None
    surprise: Optional[float] = None
    neutral: Optional[float] = None


class Region(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    w: Optional[int] = None
    h: Optional[int] = None


class Race(BaseModel):
    asian: Optional[float] = None
    indian: Optional[float] = None
    black: Optional[float] = None
    white: Optional[float] = None
    middle_eastern: Optional[float] = Field(None, alias="middle eastern")
    latino_hispanic: Optional[float] = Field(None, alias="latino hispanic")


class DeepFaceAnalysis(BaseModel):
    emotion: Optional[Emotion] = None
    dominant_emotion: Optional[str] = None
    region: Optional[Region] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    race: Optional[Race] = None
    dominant_race: Optional[str] = None