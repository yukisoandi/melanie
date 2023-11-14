from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from melanie import rcache
from runtimeopt import offloaded


@rcache(ttl="12d")
@offloaded
def get_soup_object(url) -> Optional[BeautifulSoup]:
    import httpx

    r = httpx.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36"},
    )
    return BeautifulSoup(r.content, "lxml")
