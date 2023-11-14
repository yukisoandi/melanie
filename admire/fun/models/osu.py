from __future__ import annotations

from typing import Any, Optional

from melanie import BaseModel


class Kudosu(BaseModel):
    total: Optional[int] = None
    available: Optional[int] = None


class Country(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None


class Cover(BaseModel):
    custom_url: Optional[Any] = None
    url: Optional[str] = None
    id: Optional[str] = None


class MonthlyPlaycount(BaseModel):
    start_date: Optional[str] = None
    count: Optional[int] = None


class Page(BaseModel):
    html: Optional[str] = None
    raw: Optional[str] = None


class Level(BaseModel):
    current: Optional[int] = None
    progress: Optional[int] = None


class GradeCounts(BaseModel):
    ss: Optional[int] = None
    ssh: Optional[int] = None
    s: Optional[int] = None
    sh: Optional[int] = None
    a: Optional[int] = None


class Rank(BaseModel):
    country: Optional[int] = None


class Statistics(BaseModel):
    level: Optional[Level] = None
    global_rank: Optional[int] = None
    pp: Optional[float] = None
    ranked_score: Optional[int] = None
    hit_accuracy: Optional[float] = None
    play_count: Optional[int] = None
    play_time: Optional[int] = None
    total_score: Optional[int] = None
    total_hits: Optional[int] = None
    maximum_combo: Optional[int] = None
    replays_watched_by_others: Optional[int] = None
    is_ranked: Optional[bool] = None
    grade_counts: Optional[GradeCounts] = None
    country_rank: Optional[int] = None
    rank: Optional[Rank] = None


class UserAchievement(BaseModel):
    achieved_at: Optional[str] = None
    achievement_id: Optional[int] = None


class RankHistory(BaseModel):
    mode: Optional[str] = None
    data: Optional[list[int]] = None


class RankHistory1(RankHistory):
    pass


class OsuUser(BaseModel):
    avatar_url: Optional[str] = None
    country_code: Optional[str] = None
    default_group: Optional[str] = None
    id: Optional[int] = None
    is_active: Optional[bool] = None
    is_bot: Optional[bool] = None
    is_deleted: Optional[bool] = None
    is_online: Optional[bool] = None
    is_supporter: Optional[bool] = None
    last_visit: Optional[str] = None
    pm_friends_only: Optional[bool] = None
    profile_colour: Optional[Any] = None
    username: Optional[str] = None
    cover_url: Optional[str] = None
    discord: Optional[Any] = None
    has_supported: Optional[bool] = None
    interests: Optional[str] = None
    join_date: Optional[str] = None
    kudosu: Optional[Kudosu] = None
    location: Optional[Any] = None
    max_blocks: Optional[int] = None
    max_friends: Optional[int] = None
    occupation: Optional[str] = None
    playmode: Optional[str] = None
    playstyle: Optional[list[str]] = None
    post_count: Optional[int] = None
    profile_order: Optional[list[str]] = None
    title: Optional[Any] = None
    title_url: Optional[Any] = None
    twitter: Optional[str] = None
    website: Optional[Any] = None
    country: Optional[Country] = None
    cover: Optional[Cover] = None
    account_history: Optional[list] = None
    active_tournament_banner: Optional[Any] = None
    badges: Optional[list] = None
    beatmap_playcounts_count: Optional[int] = None
    comments_count: Optional[int] = None
    favourite_beatmapset_count: Optional[int] = None
    follower_count: Optional[int] = None
    graveyard_beatmapset_count: Optional[int] = None
    groups: Optional[list] = None
    loved_beatmapset_count: Optional[int] = None
    mapping_follower_count: Optional[int] = None
    monthly_playcounts: Optional[list[MonthlyPlaycount]] = None
    page: Optional[Page] = None
    pending_beatmapset_count: Optional[int] = None
    previous_usernames: Optional[list] = None
    ranked_beatmapset_count: Optional[int] = None
    replays_watched_counts: Optional[list] = None
    scores_best_count: Optional[int] = None
    scores_first_count: Optional[int] = None
    scores_recent_count: Optional[int] = None
    statistics: Optional[Statistics] = None
    support_level: Optional[int] = None
    user_achievements: Optional[list[UserAchievement]] = None
    rankHistory: Optional[RankHistory] = None
    rank_history: Optional[RankHistory1] = None
    ranked_and_approved_beatmapset_count: Optional[int] = None
    unranked_beatmapset_count: Optional[int] = None
