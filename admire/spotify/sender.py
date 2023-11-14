import httpx
import orjson
from cashews import Cache
from tekore._sender.base import Request, Response, Sender

from melanie import get_curl, log

cache = Cache()
cache.setup("mem://?size=500")


def skit_test_result(result, args, kwargs, key=None) -> bool:
    return bool(kwargs.get("method", "GET") != "POST")


class CurlSender(Sender):
    """Send requests asynchronously.

    .. warning::

        The underlying client is **not** closed automatically.
        Use :code:`await sender.client.aclose()` to close it,
        particularly if multiple senders are instantiated.

    Parameters
    ----------
    client
        :class:`httpx.AsyncClient` to use when sending requests
    """

    def __init__(self, client=None) -> None:
        if not client:
            client = httpx.AsyncClient(http2=True)
        self.client = client
        self.debug = False

    @cache(ttl=1.2, key="{url}{method}{body}{headers}", condition=skit_test_result, lock=True)
    async def do_send(self, *, url, method, headers, body):
        curl = get_curl()
        if self.debug:
            _headers = dict(headers)
            _headers["authorization"] = "<redacted>"
            log.warning("Requests: {} {} {} {}", str(url), method, _headers, body.decode("UTF-8", "ignore") if body else "")

        return await curl.fetch(str(url), method=method, headers=headers, body=body, raise_error=False)

    async def send(self, request: Request) -> Response:
        """Send request with :class:`httpx.AsyncClient`."""
        data = None
        req = self.client.build_request(
            method=request.method,
            url=request.url,
            params=request.params,
            headers=request.headers,
            data=request.data,
            json=request.json,
            content=request.content,
        )

        data = None if req.method == "GET" else req.read()
        headers = dict(req.headers)
        headers.pop("accept-encoding", None)
        headers.pop("user-agent", None)
        headers.pop("connection", None)
        headers.pop("host", None)
        headers.pop("accept", None)
        headers.pop("cookie", None)
        r = await self.do_send(url=str(req.url), method=req.method, headers=headers, body=data)
        content = orjson.loads(r.body) if "json" in r.headers.get("content-type", {}) else None
        return Response(url=str(req.url), headers=dict(r.headers), status_code=r.code, content=content)

    @property
    def is_async(self) -> bool:
        """Sender asynchronicity, always :class:`True`."""
        return True

    async def close(self) -> None:
        """Close the underlying asynchronous client."""
        return
