from discord.http import HTTPClient, Route


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"


async def remove_embed(channel_id: str, message_id: str, http: HTTPClient):
    return await http.request(V9Route("PATCH", "/channels/{channel_id}/messages/{message_id}", channel_id=channel_id, message_id=message_id), json={"flags": 4})
