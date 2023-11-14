from __future__ import annotations

from typing import Optional

from melanie import BaseModel


class FaceRectangle(BaseModel):
    top: Optional[int] = None
    left: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class HeadPose(BaseModel):
    pitch: Optional[float] = None
    roll: Optional[float] = None
    yaw: Optional[float] = None


class FacialHair(BaseModel):
    moustache: Optional[float] = None
    beard: Optional[float] = None
    sideburns: Optional[float] = None


class Emotion(BaseModel):
    anger: Optional[float] = None
    contempt: Optional[float] = None
    disgust: Optional[float] = None
    fear: Optional[float] = None
    happiness: Optional[float] = None
    neutral: Optional[float] = None
    sadness: Optional[float] = None
    surprise: Optional[float] = None


class Blur(BaseModel):
    blurLevel: Optional[str] = None
    value: Optional[float] = None


class Exposure(BaseModel):
    exposureLevel: Optional[str] = None
    value: Optional[float] = None


class Noise(BaseModel):
    noiseLevel: Optional[str] = None
    value: Optional[float] = None


class Makeup(BaseModel):
    eyeMakeup: Optional[bool] = None
    lipMakeup: Optional[bool] = None


class Occlusion(BaseModel):
    foreheadOccluded: Optional[bool] = None
    eyeOccluded: Optional[bool] = None
    mouthOccluded: Optional[bool] = None


class HairColorItem(BaseModel):
    color: Optional[str] = None
    confidence: Optional[float] = None


class Hair(BaseModel):
    bald: Optional[float] = None
    invisible: Optional[bool] = None
    hairColor: Optional[list[HairColorItem]] = None


class FaceAttributes(BaseModel):
    smile: Optional[float] = None
    headPose: Optional[HeadPose] = None
    gender: Optional[str] = None
    age: Optional[float] = None
    facialHair: Optional[FacialHair] = None
    glasses: Optional[str] = None
    emotion: Optional[Emotion] = None
    blur: Optional[Blur] = None
    exposure: Optional[Exposure] = None
    noise: Optional[Noise] = None
    makeup: Optional[Makeup] = None
    accessories: Optional[list] = None
    occlusion: Optional[Occlusion] = None
    hair: Optional[Hair] = None


class AzureFaceAnalysis(BaseModel):
    faceId: Optional[str] = None
    faceRectangle: Optional[FaceRectangle] = None
    faceAttributes: Optional[FaceAttributes] = None
