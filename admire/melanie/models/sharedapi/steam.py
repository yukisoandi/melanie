from __future__ import annotations

from melanie import BaseModel


class SteamTrendingGame(BaseModel):
    rank: int
    name: str
    day_percent_increase: str
    current_players: int


class SteamTopCurrentGame(BaseModel):
    rank: int
    name: str
    current_players: int
    peak_players: int
    hours_played: int


class SteamChartsResponse(BaseModel):
    top_by_players: list[SteamTopCurrentGame]
    trending: list[SteamTrendingGame]
