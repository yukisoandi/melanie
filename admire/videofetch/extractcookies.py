from typing import Literal, Optional

from redis.client import Redis
from yt_dlp.cookies import YoutubeDLCookieJar

from melanie import BaseModel


class CookieModel(BaseModel):
    name: str
    value: str
    url: Optional[str]
    domain: Optional[str]
    path: Optional[str]
    expires: int = -1
    httpOnly: Optional[bool]
    secure: Optional[bool]
    sameSite: Optional[Literal["Lax", "None", "Strict"]]


class CookieData(BaseModel):
    cookies: list[CookieModel] = []


def run():
    c2 = CookieData()
    with Redis.from_url("redis://melanie.melaniebot.net", single_connection_client=True) as r:
        print(r.ping())
        import yt_dlp

        yt = yt_dlp.YoutubeDL({"cookiesfrombrowser": ("chrome",)})
        jar: YoutubeDLCookieJar = yt.cookiejar
        for c in jar:
            x = CookieModel.from_orm(c)

            c2.cookies.append(x)

        r.set("cookiedata", c2.json())
        print(c2.json())


if __name__ == "__main__":
    run()
