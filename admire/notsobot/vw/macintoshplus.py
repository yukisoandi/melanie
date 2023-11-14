# https://github.com/rickyhan/macintoshplus
"""Vaporwaveは音楽のジャンルや芸術運動である[3] [4]このようなバウンスハウス、またはchillwave、そして、より広く、エレクトロニックダンスミ
ュージック、などのインディーseapunkから2010年代初頭のダンスのジャンルに出現した。 、その態度やメッセージに多くの多様性と曖昧さ、vaporwave
がありますが:時々の両方が、大量消費社会の批判とパロディとして機能し80年代のヤッピー文化、[5]とニューエイジの音楽、音響的および審美的に彼らの/スタルジッ
クで好奇心の魅力を紹介しながら、アーティファクト。.
"""


from __future__ import annotations

import hashlib
import os
from math import cos, sin
from pathlib import Path
from random import Random, choice, randint
from typing import TYPE_CHECKING, Union

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from PIL.Image import Image

if TYPE_CHECKING:
    from _typeshed import SupportsRead


japanese_corpus: list[
    str
] = """それは20年前の今日だった
サージェント·ペッパーは、プレイするバンドを教え
彼らは、スタイルの外でと続いている
しかし、彼らは笑顔を上げることが保証している
だから私はあなたに導入することができる
あなたはこれらすべての年のために知られてきた行為
サージェント·ペパーズ·ロンリー·ハーツ·クラブ·バンド
私たちはしているサージェント·ペパーズ·ロンリー·ハーツ·クラブ·バンド
私たちは、あなたがショーを楽しむことを望む
私たちはしているサージェント·ペパーズ·ロンリー·ハーツ·クラブ·バンド
後ろに座ると夜を手放す
サージェント·ペッパーの孤独、サージェント·ペッパーの孤独
サージェント·ペパーズ·ロンリー·ハーツ·クラブ·バンド
それは素晴らしいですここに
それは確かにスリルだ
あなたはそのような素敵な観客だ
私たちは、私たちと一緒に家お連れしたいと思います
私たちは家に連れて行くのが大好きです
私は実際にショーを停止する必要はありません
しかし、私はあなたが「知りたいかもしれないと思った
歌手は歌を歌うために起こっていること
そして、私はあなたのすべてを一緒に歌うことを望んでいる
だから私はあなたに紹介しましょう
唯一無二のビリー·シアーズ
そして、サージェント·ペパーズ·ロンリー·ハーツ·クラブ·バンド""".split(
    "\n",
)
main_dir: str = str(__file__)[:-16]
bubbles: list[str] = [f"{main_dir}/img/png/bubbles/{i}" for i in os.listdir(f"{main_dir}/img/png/bubbles/") if i != "Thumbs.db"]


windows: list[str] = [f"{main_dir}/img/png/windows/{i}" for i in os.listdir(f"{main_dir}/img/png/windows/") if i != "Thumbs.db"]


backgrounds: list[str] = [f"{main_dir}/img/png/background/{i}" for i in os.listdir(f"{main_dir}/img/png/background/") if i != "Thumbs.db"]


pics: list[str] = [f"{main_dir}/img/png/pics/{i}" for i in os.listdir(f"{main_dir}/img/png/pics/") if i != "Thumbs.db"]


greek: list[str] = [f"{main_dir}/img/png/greek/{i}" for i in os.listdir(f"{main_dir}/img/png/greek/") if i != "Thumbs.db"]


def random_color(k: int = 0) -> tuple[int, ...]:
    return (int(k % 255), int(255 * cos(k)), int(255 * (1 - sin(k))))


def full_width(txt):
    """Translate to unicode letters."""
    WIDE_MAP = {i: i + 0xFEE0 for i in range(0x21, 0x7F)}
    WIDE_MAP[0x20] = 0x3000
    return str(txt).translate(WIDE_MAP)


def draw_text(txt: Union[bytes, str], image: Image, k: int = 0, x: int = 0, y: int = 30) -> Image:
    """Takes a image and places txt on it."""
    font_path = f"{main_dir}/resources/arial.ttf"
    draw = ImageDraw.Draw(image)

    # autofit
    fontsize = 1  # starting font size
    # portion of image width you want text width to be
    img_fraction = 0.50
    font = ImageFont.truetype(font_path, fontsize)
    while font.getsize(txt)[0] < img_fraction * image.size[0] * 0.7:
        # iterate until the text size is just larger than the criteria
        fontsize += 1
        font = ImageFont.truetype(font_path, fontsize)

    txt = full_width(txt)
    # #############
    # # thin border

    # thicker border
    draw.text((x - 2, y - 2), txt, font=font, fill=random_color(k + 90))
    draw.text((x + 2, y - 2), txt, font=font, fill=random_color(k + 60))
    draw.text((x - 2, y + 2), txt, font=font, fill=random_color(k + 37))
    draw.text((x + 2, y + 2), txt, font=font, fill=random_color(k + 80))
    #################

    return image


def insert_bubble(foreground_path: Union[SupportsRead[bytes], bytes, Path, str], im):
    """Insert notification bubble on the bottom right corner."""
    foreground = Image.open(foreground_path)
    background_size = im.size
    foreground_size = foreground.size
    im.paste(foreground, (background_size[0] - foreground_size[0], background_size[1] - foreground_size[1]), foreground)
    return im


def insert_window_as_background(foreground_path: Union[SupportsRead[bytes], bytes, Path, str], im, k: int = 0):
    """Fractal generative art, not a great idea for vaporwave though.

    not ironic enough

    """
    foreground = Image.open(foreground_path)
    background_size = im.size
    foreground_size = foreground.size
    ratio = float(foreground_size[0]) / float(foreground_size[1])
    for i in range(500, 600):  # the step determines the distances between
        pos = (
            int((background_size[0] - foreground_size[0]) / 2 + i * sin(i) / sin(k)),
            int((background_size[1] - foreground_size[1]) / 2 + i * cos(i) / ratio / cos(k)),
        )
        try:
            im.paste(foreground, pos, foreground)
        except ValueError:
            im.paste(foreground, pos)
    return im


def insert_cascade(foreground_path: Union[SupportsRead[bytes], bytes, Path, str], im, k: int = 0, x: int = 100, y: int = 100):
    """Another postironic function.

    raster box drawing

    """
    foreground = Image.open(foreground_path)
    background_size = im.size
    foreground_size = foreground.size
    acc = -1
    v = 0.0
    dy = 0.0
    for i in range(int(k * 100)):  # the step determines the distances between
        dy = v * i + 0.5 * acc * (i**2)
        v += acc * i
        pos = (int(x + 11 * i), int(y - dy))
        im.paste(foreground, pos)
        if background_size[1] - foreground_size[1] <= pos[1] + foreground_size[1]:
            v = -v
            acc *= 0.9

    return im


def insert_window_as_background2(foreground_path: Union[SupportsRead[bytes], bytes, Path, str], im):
    """Another postironic function.

    raster box drawing

    """
    foreground = Image.open(foreground_path)
    background_size = im.size
    foreground_size = foreground.size
    float(foreground_size[0]) / float(foreground_size[1])
    for i in range(0, 100, 10):  # the step determines the distances between
        pos = (int((background_size[0] - foreground_size[0]) / 2 + i), int((background_size[1] - foreground_size[1]) / 2 - i))
        im.paste(foreground, pos)
    return im


def horizon(background_path: Union[SupportsRead[bytes], bytes, Path, str], im):
    """Stretch a picture for horizontal perspective.

    math is hard

    """
    background = Image.open(background_path)
    # WWWWWWWWWWWWTTTTTTTTTTTTTTTTTTTFFFFFFFFFFFFFFFFFFFFFFF MATH???? :-K
    im.paste(background, (0, 0))
    return im


def insert_pic(foreground_path: Union[SupportsRead[bytes], bytes, Path, str], im, k: int = 0, x: int = 0, y: int = 1000):
    """Add Vaporwaveは音楽のジャンルや芸術運動である style pic.

    k is for nuanced transformations such as rotation and oscillation

    """
    foreground = Image.open(foreground_path)
    im.size
    foreground_size = foreground.size
    float(foreground_size[0]) / float(foreground_size[1])
    pos = (x, y)
    foreground = foreground.rotate(k * 100)
    im.paste(foreground, pos, foreground)
    return im


def color(im, k: float = 3) -> Image:
    enhancer = ImageEnhance.Color(im)
    return enhancer.enhance(k)


def contrast(im, k: float = 3) -> Image:
    enhancer = ImageEnhance.Contrast(im)
    return enhancer.enhance(k)


def sharpness(im, k: float = 3) -> Image:
    enhancer = ImageEnhance.Sharpness(im)
    return enhancer.enhance(k)


def brightness(im, k: float = 3) -> Image:
    enhancer = ImageEnhance.Brightness(im)
    return enhancer.enhance(k)


def smooth(im, k: int = 3):
    return im.filter(ImageFilter.SMOOTH)


def hashseed(seedtext) -> str:
    return hashlib.sha224(seedtext.encode()).hexdigest()


def draw_method1(k, name, im):
    """Non-ironic function."""
    seedvalue = str(randint(0, 999999)) if name == "vapor wave" else hashseed(name)
    x, y = (1000, 1000)
    im = insert_cascade(Random(seedvalue + str(0)).choice(windows), im, k=0.5)
    im = insert_pic(Random(seedvalue + str(1)).choice(pics), im, k=0, x=x // 2, y=y // 2)

    im = insert_pic(Random(seedvalue + str(3)).choice(pics), im, k=0, x=x // 2, y=0)

    im = insert_pic(choice(pics), im, x=randint(0, im.height), y=randint(0, im.width))
    im = insert_pic(Random(seedvalue + str(3)).choice(greek), im, k=0, x=0, y=int(y / 2.5 + 50))
    im = insert_window_as_background(Random(seedvalue + str(7)).choice(pics), im, k=44)
    im = insert_bubble(Random(seedvalue + str(5)).choice(bubbles), im)
    im = draw_text(Random(seedvalue + str(6)).choice(japanese_corpus), im, k=500, y=y // 2)

    im = draw_text(name, im, k=Random(seedvalue + str(13)).randint(100, 500), x=50, y=y // 2)

    im = smooth(im, Random(f"{seedvalue}:)").randint(3, 10))
    im = color(im, Random(seedvalue).randint(3, 10))
    return im


if __name__ == "__main__":
    # k is for nuanced transformations in individual functions
    # for k in range(100,200):
    # 	print k, '------------'
    # 	im = draw_method1(float(k)/100,'MOFO NIGGER')
    # 	im.save('animated\\'+str(k)+'.png')

    ############################################
    k = 95
    im = draw_method1(k // 100, name="REDDIT")
    im.save("100.png")
