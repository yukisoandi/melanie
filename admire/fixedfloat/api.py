from __future__ import annotations

import hashlib
import hmac
from typing import Optional
from urllib.parse import urlencode

import orjson

from melanie import CurlRequest, get_curl

from .models.create_order import FixedFloatCreateOrderResponse
from .models.currency import FixedFloatCurrency
from .models.get_order import FixedFloatGetOrderResponse


class FixedFloatAPI:
    def __init__(self, API_KEY: str, SECRET_KEY: str) -> None:
        self._API_KEY = API_KEY
        self._SECRET_KEY = SECRET_KEY
        self._MAIN_URL = "https://fixedfloat.com/api/v1/"

    async def _sendRequest(self, reqMethod: str = None, apiMethod: str = None, body: str = "") -> Optional[dict]:
        if reqMethod and apiMethod:
            headers = {
                "X-API-KEY": self._API_KEY,
                "X-API-SIGN": hmac.new(self._SECRET_KEY.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest(),
                "Content-Type": "application/x-www-form-urlencoded",
            }

            curl = get_curl()
            if reqMethod == "GET":
                request = CurlRequest(self._MAIN_URL + apiMethod + "?" + body, headers=headers, method=reqMethod)
            elif reqMethod == "POST" and body != "":
                request = CurlRequest(self._MAIN_URL + apiMethod, headers=headers, body=body, method=reqMethod)
            else:
                return None

            r = await curl.fetch(request)

            return orjson.loads(r.body)

    async def get_currencies(self):
        """Getting a list of all currencies that are available on FixedFloat.com."""
        data = await self._sendRequest("GET", "getCurrencies")
        _data = [FixedFloatCurrency.parse_obj(x) for x in data["data"]]
        return sorted(_data, key=lambda x: x.symbol)

    async def get_price(self, fromCurrency: str, toCurrency: str, fromQty: float, toQty: float = 0.00, type: str = "float"):
        """Information about a currency pair with a set amount of funds."""
        body = urlencode({"fromCurrency": fromCurrency, "toCurrency": toCurrency, "fromQty": float(fromQty), "toQty": float(toQty), "type": type})
        return await self._sendRequest("POST", "getPrice", body)

    async def get_order(self, id: str, token: str) -> FixedFloatGetOrderResponse:
        """Receiving information about the order."""
        body = urlencode({"id": id, "token": token})
        return FixedFloatGetOrderResponse.parse_obj(await self._sendRequest("GET", "getOrder", body))

    async def set_emergency(self, id: str, token: str, choice: str, address: str = ""):
        """Emergency Action Choice."""
        body = urlencode({"id": id, "token": token, "choice": choice, "address": address})
        return await self._sendRequest("GET", "setEmergency", body)

    async def create_order(
        self,
        fromCurrency: str,
        toCurrency: str,
        toAddress: str,
        fromQty: float,
        toQty: float = 0.00,
        type: str = "float",
        extra: str = "",
    ) -> FixedFloatCreateOrderResponse:
        """Creating exchange orders."""
        body = urlencode(
            {
                "fromCurrency": fromCurrency,
                "toCurrency": toCurrency,
                "fromQty": float(fromQty),
                "toQty": float(toQty),
                "toAddress": toAddress,
                "extra": extra,
                "type": type,
            },
        )

        return FixedFloatCreateOrderResponse.parse_obj(await self._sendRequest("POST", "createOrder", body))
