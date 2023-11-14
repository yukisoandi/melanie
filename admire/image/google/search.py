from typing import Optional

import orjson
import regex as re
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from melanie import BaseModel, get_curl, url_concat


class SearchItem(BaseModel):
    title: str
    link: str
    source: Optional[str]
    original: Optional[str]
    thumbnail: Optional[str]


class SearchResult(BaseModel):
    items: list[SearchItem] = []


async def fetch_web_html(query, safe=True) -> str:
    from melanie import log

    curl = get_curl()

    ua = UserAgent()

    url = "https://chrome.melaniebot.net/content"
    params = {
        "tbm": "isch",
        "safe": "on" if safe else "off",
        "q": query,
    }
    payload = {
        "setJavaScriptEnabled": "true",
        "url": url_concat("https://www.google.com/search", params),
        "userAgent": str(ua.random),
    }
    headers = {"Content-Type": "application/json", "Accept": "text/html"}
    querystring = {
        "blockAds": "true",
        "headless": '"new"',
        "ignoreHTTPSErrors": "true",
        "stealth": "true",
    }

    r = await curl.fetch(
        url_concat(url, querystring),
        body=orjson.dumps(payload),
        headers=headers,
        method="POST",
    )
    if _url := r.headers.get("x-response-url"):
        log.info(_url)

    return r.body.decode("UTF-8", "replace")


def process_search_markup(markup: str) -> str:
    soup = BeautifulSoup(markup, "lxml")
    all_script_tags = soup.select("script")
    matched_images_data = "".join(re.findall(r"AF_initDataCallback\(([^<]+)\);", str(all_script_tags)))
    fix = orjson.dumps(matched_images_data)
    match_json = orjson.loads(fix)
    matched_google_image_data = re.findall(r"\"b-GRID_STATE0\"(.*)sideChannel:\s?{}}", match_json)
    matched_google_images_thumbnails = ", ".join(
        re.findall(
            r"\[\"(https\:\/\/encrypted-tbn0\.gstatic\.com\/images\?.*?)\",\d+,\d+\]",
            str(matched_google_image_data),
        ),
    ).split(", ")

    thumbnails = [bytes(bytes(thumbnail, "ascii").decode("unicode-escape"), "ascii").decode("unicode-escape") for thumbnail in matched_google_images_thumbnails]

    rm_mtch = re.sub(
        r"\[\"(https\:\/\/encrypted-tbn0\.gstatic\.com\/images\?.*?)\",\d+,\d+\]",
        "",
        str(matched_google_image_data),
    )

    matched_4k = re.findall(r"(?:'|,),\[\"(https:|http.*?)\",\d+,\d+\]", rm_mtch)

    full_res_images = [bytes(bytes(img, "ascii").decode("unicode-escape"), "ascii").decode("unicode-escape") for img in matched_4k]

    google_images = [
        {
            "title": metadata.select_one(".iGVLpd.kGQAp.BqKtob.lNHeqe")["title"],
            "link": metadata.select_one(".iGVLpd.kGQAp.BqKtob.lNHeqe")["href"],
            "source": metadata.select_one(".LAA3yd").text,
            "thumbnail": thumbnail,
            "original": original,
        }
        for metadata, thumbnail, original in zip(soup.select(".isv-r.PNCib.ViTmJb.BUooTd"), thumbnails, full_res_images)
    ]
    result = SearchResult()
    for x in google_images:
        result.items.append(SearchItem(**x))
    return result.json()
