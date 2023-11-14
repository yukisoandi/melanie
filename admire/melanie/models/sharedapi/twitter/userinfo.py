from __future__ import annotations

from melanie import BaseModel
from melanie.models.sharedapi.twitter.graphql import TweetEntryItem  # noqa
from melanie.models.sharedapi.twitter.screen_name import TwitterUserinfoResult  # noqa


class TwitterUserDataRaw(BaseModel):
    suspended: bool | None = False
    info: TwitterUserinfoResult | None = None
    tweets: list[TweetEntryItem] | (bool | None) = None
