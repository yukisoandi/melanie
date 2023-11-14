# https://github.com/NotSoSuper/NotSoBot


from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from asyncio.transports import BaseTransport
from io import BytesIO
from pathlib import Path
from typing import Optional

import discord
import regex as re
from PIL import Image, ImageDraw, ImageFont, ImageSequence
from regex.regex import Pattern

retro_regex: Pattern[str] = re.compile(r"((https)(\:\/\/|)?u2?\.photofunia\.com\/.\/results\/.\/.\/.*(\.jpg\?download))")
image_mimes = ["image/png", "image/pjpeg", "image/jpeg", "image/x-icon"]
gif_mimes = ["image/gif"]
emoji_map = {
    "a": "",
    "b": "",
    "c": "©",
    "d": "↩",
    "e": "",
    "f": "",
    "g": "⛽",
    "h": "♓",
    "i": "i",
    "j": "\uf336",
    "k": "",
    "l": "",
    "m": "Ⓜ",
    "n": "♑",
    "o": "⭕",
    "p": "",
    "q": "",
    "r": "®",
    "s": "\uf4b2",
    "t": "",
    "u": "⛎",
    "v": "\uf596",
    "w": "〰",
    "x": "❌",
    "y": "✌",
    "z": "Ⓩ",
    "1": "1⃣",
    "2": "2⃣",
    "3": "3⃣",
    "4": "4⃣",
    "5": "5⃣",
    "6": "6⃣",
    "7": "7⃣",
    "8": "8⃣",
    "9": "9⃣",
    "0": "0⃣",
    "$": "",
    "!": "❗",
    "?": "❓",
    " ": "　",
}


class DataProtocol(asyncio.SubprocessProtocol):
    def __init__(self, exit_future) -> None:
        self.exit_future = exit_future
        self.output = bytearray()

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        self.output.extend(data)

    def process_exited(self) -> None:
        with contextlib.suppress(Exception):
            self.exit_future.set_result(True)

    def pipe_connection_lost(self, fd: int, exc: Optional[Exception]) -> None:
        with contextlib.suppress(Exception):
            self.exit_future.set_result(True)

    def connection_made(self, transport: BaseTransport) -> None:
        self.transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        with contextlib.suppress(Exception):
            self.exit_future.set_result(True)


code = "```py\n{0}\n```"


def posnum(num):
    return -(num) if num < 0 else num


def make_merge(list_im, vertical: bool = True):
    import numpy as np

    imgs = [Image.open(i).convert("RGBA") for i in list_im]
    if vertical:
        # Vertical
        max_shape = sorted([(np.sum(i.size), i.size) for i in imgs])[1][1]
        imgs_comb = np.vstack([np.asarray(i.resize(max_shape)) for i in imgs])
    else:
        # Horizontal
        min_shape = sorted([(np.sum(i.size), i.size) for i in imgs])[0][1]
        imgs_comb = np.hstack([np.asarray(i.resize(min_shape)) for i in imgs])
    imgs_comb = Image.fromarray(imgs_comb)
    final = BytesIO()
    imgs_comb.save(final, "png")
    file_size = final.tell()
    final.seek(0)
    file = discord.File(final, filename="merge.png")
    final.close()
    for i in imgs:
        i.close()
    return (file, file_size)


def find_coeffs(pa, pb):
    import numpy as np

    matrix = []
    for p1, p2 in zip(pa, pb):
        matrix.extend(([p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]], [0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]]))

    A = np.matrix(matrix, dtype=float)
    B = np.array(pb).reshape(8)
    res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
    return np.array(res).reshape(8)


def make_beautiful_gif(template: Image, avatar: Image) -> BytesIO:
    gif_list = [frame.copy() for frame in ImageSequence.Iterator(avatar)]
    img_list = []
    num = 0
    temp = None
    for frame in gif_list:
        template = template.convert("RGBA")
        frame = frame.convert("RGBA")
        template.paste(frame, (370, 45), frame)
        template.paste(frame, (370, 330), frame)
        img_list.append(template)
        num += 1
        temp = BytesIO()
        template.save(temp, format="GIF", save_all=True, append_images=img_list, duration=0, loop=0)
        temp.name = "beautiful.gif"
        if sys.getsizeof(temp) < 8000000 and sys.getsizeof(temp) > 7000000:
            break
    return temp


def make_beautiful_img(template: Image, avatar: Image) -> BytesIO:
    template = template.convert("RGBA")
    avatar = avatar.convert("RGBA")
    template.paste(avatar, (370, 45), avatar)
    template.paste(avatar, (370, 330), avatar)
    temp = BytesIO()
    template.save(temp, format="PNG")
    temp.name = "beautiful.png"
    return temp


def computeAndLoadTextFontForSize(drawer: ImageDraw.Draw, text: str, maxWidth: int, data_path) -> ImageFont:
    # global textFont
    data_path = Path(data_path)

    # Measure text and find out position
    maxSize = 50
    minSize = 6
    curSize = maxSize
    textFont = None
    while curSize >= minSize:
        textFont = ImageFont.truetype(f"{str(data_path)}/impact.ttf", size=curSize)
        (w, h) = drawer.textsize(text, font=textFont)

        if w > maxWidth:
            curSize -= 4
        else:
            return textFont
    return textFont


def rotoscope(dst, warp, properties: dict):
    import cv2
    import numpy as np

    if not properties["show"]:
        return dst

    corners = properties["corners"]

    wRows, wCols, wCh = warp.shape
    rows, cols, ch = dst.shape

    # Apply blur on warp
    kernel = np.ones((5, 5), np.float32) / 25
    warp = cv2.filter2D(warp, -1, kernel)

    # Prepare points to be matched on Affine Transformation
    pts1 = np.float32([[0, 0], [wCols, 0], [0, wRows]])
    pts2 = np.float32(corners) * 2

    # Enlarge image to multisample
    dst = cv2.resize(dst, (cols * 2, rows * 2))

    # Transform image with the Matrix
    M = cv2.getAffineTransform(pts1, pts2)
    cv2.warpAffine(warp, M, (cols * 2, rows * 2), dst, flags=cv2.INTER_AREA, borderMode=cv2.BORDER_TRANSPARENT)

    # Sample back image size
    dst = cv2.resize(dst, (cols, rows))

    return dst


def computeAndLoadTextFontForSize(drawer: ImageDraw.Draw, text: str, maxWidth: int) -> ImageFont:
    # global textFont

    # Measure text and find out position
    maxSize = 50
    minSize = 6
    curSize = maxSize
    textFont = None
    while curSize >= minSize:
        textFont = ImageFont.truetype("/home/melanie/data/cogs/CogManager/cogs/notsobot/data/impact.ttf", size=curSize)
        w, h = drawer.textsize(text, font=textFont)

        if w > maxWidth:
            curSize -= 4
        else:
            return textFont
    return textFont


def cvImageToPillow(cvImage) -> Image:
    import cv2

    cvImage = cv2.cvtColor(cvImage, cv2.COLOR_BGR2RGB)
    return Image.fromarray(cvImage)


def make_trump_gif(text: str) -> tuple[Optional[discord.File], int]:
    import cv2

    f"trump:{text.lower().strip()}"

    folder = Path("/cache/botdata/cogs/CogManager/cogs/notsobot/data/trump_template")

    jsonPath = os.path.join(folder, "frames.json")

    # Load frames
    frames = json.load(open(jsonPath))

    # Used to compute motion blur
    textImage = generateText(text)

    # Will store all gif frames
    frameImages = []

    # Iterate trough frames
    for frame in frames:
        # Load image
        name = frame["file"]
        filePath = os.path.join(folder, name)
        finalFrame = None

        # If it has transformations,
        # process with opencv and convert back to pillow
        if frame["show"]:
            image = cv2.imread(filePath)

            # Do rotoscope
            image = rotoscope(image, textImage, frame)

            # Show final result
            finalFrame = cvImageToPillow(image)
        else:
            finalFrame = Image.open(filePath)

        frameImages.append(finalFrame)
    temp = BytesIO()
    # Saving...
    frameImages[0].save(temp, format="GIF", save_all=True, append_images=frameImages, duration=0, loop=0)
    temp.name = "Trump.gif"
    temp.seek(0)
    finalFrame.close()
    data = temp.getvalue()
    size = len(data)

    return data, size


def computeAndLoadTextFontForSize(drawer: ImageDraw.Draw, text: str, maxWidth: int) -> ImageFont:
    # global textFont

    # Measure text and find out position
    maxSize = 50
    minSize = 6
    curSize = maxSize
    textFont = None
    while curSize >= minSize:
        textFont = ImageFont.truetype("/home/melanie/data/cogs/CogManager/cogs/notsobot/data/impact.ttf", size=curSize)
        w, h = drawer.textsize(text, font=textFont)
        if w > maxWidth:
            curSize -= 4
        else:
            return textFont
    return textFont


def generateText(text: str):
    # global impact, textFont

    import cv2
    import numpy as np

    txtColor = (20, 20, 20)
    bgColor = (224, 233, 237)
    imgSize = (160, 200)

    # Create image
    image = Image.new("RGB", imgSize, bgColor)

    # Draw text on top
    draw = ImageDraw.Draw(image)

    # Load font for text
    textFont = computeAndLoadTextFontForSize(draw, text, imgSize[0])

    w, h = draw.textsize(text, font=textFont)
    xCenter = (imgSize[0] - w) / 2
    yCenter = (50 - h) / 2
    draw.text((xCenter, 10 + yCenter), text, font=textFont, fill=txtColor)
    impact = ImageFont.truetype("/home/melanie/data/cogs/CogManager/cogs/notsobot/data/impact.ttf", 46)
    draw.text((12, 70), "IS NOW", font=impact, fill=txtColor)
    draw.text((10, 130), "ILLEGAL", font=impact, fill=txtColor)

    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
