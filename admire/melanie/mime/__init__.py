from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from tornado.escape import url_unescape

from .db import data as _mime_data_table


def guess_type_from_url(url: str) -> tuple[str, str]:
    """guess_type_from_url Create a mime/type & extension pair given a provided
    link.

    Returns
    -------
    tuple[str, str]
        ('image/webp', '.web')

    """
    _url = urlparse(url_unescape(url))
    _path = Path(_url.path)
    _path.name
    mime = _mime_data_table.get(_path.suffix)
    ext = _path.suffix
    return mime, ext
