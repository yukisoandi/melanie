from __future__ import annotations

import os

from tornado.httpclient import HTTPResponse

from .curl import CurlRequest, get_curl
from .timing import capturetime


async def upload_file(name: str, body: bytes) -> HTTPResponse:
    with capturetime(f"uploads {name}"):
        curl = get_curl()
        url = f"https://storage.bunnycdn.com/melanieapidocs/{name}"
        _r = CurlRequest(
            url=url,
            method="PUT",
            headers={"AccessKey": str(os.environ["BUNNY_KEY"]), "content-type": "application/octet-stream"},
            body=body,
            request_timeout=0,
        )
        return await curl.fetch(_r)
