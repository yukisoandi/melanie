from __future__ import annotations

from base64 import b64encode

from discord.http import Route
from filetype.filetype import guess_mime

from melanie.curl import worker_download
from runtimeopt import offloaded


class V9Route(Route):
    BASE: str = "https://discord.com/api/v9"


def _bytes_to_base64_data(data: bytes) -> str:
    fmt = "data:{mime};base64,{data}"
    mime = guess_mime(data)
    b64 = b64encode(data).decode("ascii")
    return fmt.format(mime=mime, data=b64)


@offloaded
def convert_img_to(url: str, format: str = "png") -> bytes:
    from wand.image import Image

    content = worker_download(url)
    i = Image(blob=content)
    return i.make_blob(format=format)


async def role_icon(ctx, guild_id, role_id, icon) -> None:
    unicode_emoji = None
    if isinstance(icon, str):
        unicode_emoji = icon
        icon = None
    if isinstance(icon, bytes):
        unicode_emoji = None
        icon = _bytes_to_base64_data(icon)

    payload = {"unicode_emoji": unicode_emoji, "icon": icon}

    route = V9Route("PATCH", "/guilds/{guild_id}/roles/{role_id}", guild_id=guild_id, role_id=role_id)

    await ctx.cog.bot.http.request(route, json=payload)
