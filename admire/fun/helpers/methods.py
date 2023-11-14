from __future__ import annotations

import io
import os
from typing import Optional

import requests
from filetype import guess_extension

from fun.models.az_face import AzureFaceAnalysis
from fun.models.deepface import DeepFaceAnalysis
from fun.models.osu import OsuUser
from melanie.curl import worker_download
from runtimeopt import offloaded

FACE_API_KEY: Optional[str] = os.getenv("FACE_API_KEY")
FACE_API_HOST: Optional[str] = os.getenv("FACE_API_HOST")


@offloaded
def get_osu_user(client_id, client_secret, username) -> OsuUser:
    data = {"client_id": f"{client_id}", "client_secret": f"{client_secret}", "grant_type": "client_credentials", "scope": "public"}
    auth_request = requests.post("https://osu.ppy.sh/oauth/token", json=data).json()
    header = {"Authorization": f"Bearer {auth_request['access_token']}"}
    r = requests.get(f"https://osu.ppy.sh/api/v2/users/{username}", headers=header).json()
    return OsuUser(**r)


@offloaded
def generate_bigmoji4(img_url, format):
    import cairosvg
    from wand.image import Image

    img_bytes = worker_download(img_url)
    if not img_bytes:
        return None
    if format == "svg":
        kwargs = {"parent_width": 1024, "parent_height": 1024}
        data = cairosvg.svg2png(bytestring=img_bytes, **kwargs)
    else:
        format = guess_extension(img_bytes)
        with Image(blob=img_bytes) as i:
            factor = 300 // i.width
            height2 = i.height * factor
            width2 = i.width * factor
            i.interlace_scheme = format
            i.quantize(250)
            i.coalesce()
            i.optimize_layers()
            i.resize(width=width2, height=height2, filter="lanczos2sharp")
            data = i.make_blob(format=format)
    return data


def deepface_scan(image_url: str) -> DeepFaceAnalysis:
    import numpy as np
    from PIL import Image as PILImage

    np.array(PILImage.open(io.BytesIO(requests.get(image_url).content)))


def azure_face_scan(image_url: str) -> AzureFaceAnalysis:
    headers = {"Ocp-Apim-Subscription-Key": FACE_API_KEY}

    body = {"url": image_url}
    url = f"https://{FACE_API_HOST}/face/v1.0/detect"
    params = {
        "returnFaceAttributes": "accessories,age,blur,emotion,exposure,facialhair,gender,glasses,hair,headpose,makeup,noise,occlusion,smile",
        "detectionModel": "detection_01",
    }

    r = requests.get(url, json=body, params=params, headers=headers)
    res = r.json()[0]

    return AzureFaceAnalysis(**res)
