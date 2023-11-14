import asyncio
import logging
from collections.abc import Awaitable
from enum import Enum, auto

import websockets
from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed

from melanie import SHARED_API_HEADERS, BaseModel, get_curl, log, url_concat


class StrEnum(str, Enum):
    def _generate_next_value_(self, start, count, last_values) -> str:
        return self


class DeletionConfirmation(BaseModel):
    confirmed: bool
    confirmed_by: list[str] = []
    deleted_items: int | None = 0
    sig: str


class OpCodes(StrEnum):
    SNIPEDEL: str = auto()
    SNIPEDEL_ACK: str = auto()


class SnipeDeleteRequest(BaseModel):
    channel_id: int
    api_username: str
    sig: str

    @property
    def ack_key(self) -> str:
        return f"snipedel_ack:{self.sig}"


class SnipeAckMessage(BaseModel):
    sig: str
    deleted_items: int | None = None


class WebsocketMessage(BaseModel):
    op: OpCodes
    data: SnipeDeleteRequest | SnipeAckMessage | None


class SharedApiSocket:
    def __init__(self, api_headers: dict) -> None:
        self.connection_args = {
            "uri": "wss://dev.melaniebot.net/api/discord/ws",
            "extra_headers": api_headers,
            "ping_interval": 10,
            "user_agent_header": "melaniebot snipe socket",
        }

        logger = logging.getLogger("sharedapi.socket")
        logger.setLevel("ERROR")
        self.connection_args["logger"] = logger

    async def run(self, deletion_handler: Awaitable):
        """Run the SharedAPI websocket handler.

        Args:
        ----
            deletion_handler (Awaitable): A coroutine that will be executed when a snipe deletion request is sent inbound. It will receive a single argument being the SnipeDeleteRequest.

            deletion_handler should return either None (if the bot does not have the channel in view), or an int (number of items deleted from the snipe cache)
            If the number of items deleted is not provided but the channel is visable, return 0 to confirm snipe cache was cleared

        """
        if not asyncio.iscoroutinefunction(deletion_handler):
            msg = "The deletion callback handler should be a coroutine"
            raise ValueError(msg)
        async for ws in websockets.connect(**self.connection_args):
            try:
                async for data in ws:
                    try:
                        msg = WebsocketMessage.parse_raw(data)
                    except ValidationError:
                        continue
                    if msg.op == OpCodes.SNIPEDEL:
                        with log.catch(exclude=asyncio.CancelledError):
                            async with asyncio.timeout(5):
                                deleted_items = await deletion_handler(msg.data)
                                if deleted_items is not None:
                                    await ws.send(
                                        WebsocketMessage(op=OpCodes.SNIPEDEL_ACK, data=SnipeAckMessage(sig=msg.data.sig, deleted_items=deleted_items)).json(),
                                    )
                                    log.success("Issued snipe deletion ack for {}", deleted_items)

            except ConnectionClosed as e:
                log.error("Websocket closed - Restarting  {}", e)
                continue

    async def submit_snipedel_request(self, channel_id: int) -> DeletionConfirmation:
        curl = get_curl()
        url = url_concat("https://dev.melaniebot.net/api/discord/clearsnipe", {"channel_id": channel_id})
        r = await curl.fetch(url, method="DELETE", headers=SHARED_API_HEADERS)
        return DeletionConfirmation.parse_raw(r.body)
