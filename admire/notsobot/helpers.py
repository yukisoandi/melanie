from __future__ import annotations

import itertools
import random
import sys
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Union

import discord
import wand
import wand.color
import wand.drawing
from filetype import guess_mime
from loguru import logger as log
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence
from pyfiglet import figlet_format

from melanie import get_curl, rcache
from notsobot.vw import macintoshplus
from runtimeopt import offloaded

if TYPE_CHECKING:
    from _typeshed import SupportsRead


BASE_DIR: Path = Path(__file__).parent / "data"


async def bytes_download(url: Union[discord.Asset, discord.Attachment, str]) -> tuple[Union[BytesIO, bool], Union[str, bool]]:
    url = str(url)
    curl = get_curl()
    r = await curl.fetch(url)
    return r.buffer, guess_mime(r.body)


def bytes_to_image(image: BytesIO, size: int):
    image = Image.open(image).convert("RGBA")
    image = image.resize((size, size))
    return image


def gen_neko(member_avatar):
    member_avatar = bytes_to_image(member_avatar, 156)
    # base canvas
    im = Image.new("RGBA", (500, 750), None)
    nekomask = Image.open(f"{BASE_DIR}/neko/nekomask.png", mode="r").convert("RGBA")

    # pasting the pfp
    im.paste(member_avatar, (149, 122), member_avatar)
    im.paste(nekomask, (0, 0), nekomask)
    nekomask.close()
    member_avatar.close()

    fp = BytesIO()
    im.save(fp, "PNG")
    fp.seek(0)
    im.close()
    _file = discord.File(fp, "neko.png")
    fp.close()
    return _file


def gen_bonk(victim_avatar: Union[None, BytesIO], bonker_avatar=None):
    # base canvas
    im = Image.open(f"{BASE_DIR}/bonk/bonkbase.png", mode="r").convert("RGBA")

    # pasting the victim
    victim_avatar = bytes_to_image(victim_avatar, 256)
    victim_avatar = victim_avatar.rotate(angle=10, resample=Image.BILINEAR)
    im.paste(victim_avatar, (650, 225), victim_avatar)
    victim_avatar.close()

    # pasting the bonker
    if bonker_avatar:
        bonker_avatar = bytes_to_image(bonker_avatar, 223)
        im.paste(bonker_avatar, (206, 69), bonker_avatar)
        bonker_avatar.close()

    # pasting the bat
    bonkbat = Image.open(f"{BASE_DIR}/bonk/bonkbat.png", mode="r").convert("RGBA")
    im.paste(bonkbat, (452, 132), bonkbat)
    bonkbat.close()

    fp = BytesIO()
    im.save(fp, "PNG")
    fp.seek(0)
    im.close()
    _file = discord.File(fp, "bonk.png")
    fp.close()
    return _file


def gen_simp(member_avatar: Union[float, BytesIO, str, tuple[float], tuple[int, ...]]):
    member_avatar = bytes_to_image(member_avatar, 136)
    # base canvas
    im = Image.new("RGBA", (500, 319), None)
    card = Image.open(f"{BASE_DIR}/simp/simp.png", mode="r").convert("RGBA")

    # pasting the pfp
    member_avatar = member_avatar.rotate(angle=3, resample=Image.BILINEAR, expand=True)
    im.paste(member_avatar, (73, 105))
    member_avatar.close()

    # pasting the card
    im.paste(card, (0, 0), card)
    card.close()

    fp = BytesIO()
    im.save(fp, "PNG")
    fp.seek(0)
    im.close()
    _file = discord.File(fp, "simp.png")
    fp.close()
    return _file


def gen_horny(member_avatar: Union[float, BytesIO, str, tuple[float], tuple[int, ...]]):
    member_avatar = bytes_to_image(member_avatar, 85)
    # base canvas
    im = Image.new("RGBA", (360, 300), None)
    card = Image.open(f"{BASE_DIR}/horny/horny.png", mode="r").convert("RGBA")

    # pasting the pfp
    member_avatar = member_avatar.rotate(angle=22, resample=Image.BILINEAR, expand=True)
    im.paste(member_avatar, (43, 117))
    member_avatar.close()

    # pasting the card
    im.paste(card, (0, 0), card)
    card.close()

    fp = BytesIO()
    im.save(fp, "PNG")
    fp.seek(0)
    im.close()
    _file = discord.File(fp, "horny.png")
    fp.close()
    return _file


@rcache(ttl="1d")
@offloaded
def api_make_text2(color: str, txt: str) -> bytes:
    import html

    import pyvips

    txt = txt.strip()[:2000]
    txt = html.escape(txt)
    wordstr = f"<span foreground='#{color}'>{txt}</span>"
    image_obj = pyvips.Image.text(wordstr, font="Segoe UI", width=750, height=250, rgba=True, align="centre")
    return image_obj.write_to_buffer(".webp", preset="text", strip=True)


def do_glitch(b: Union[SupportsRead[bytes], bytearray, bytes, BytesIO, Path, str], amount, seed, iterations):
    img = Image.open(b)
    import jpglitch  # type: ignore

    is_gif = img.is_animated
    if not is_gif:
        img = img.convert("RGB")
        b = BytesIO()
        img.save(b, format="JPEG")
        b.seek(0)
        img = jpglitch.Jpeg(bytearray(b.getvalue()), amount, seed, iterations)
        final = BytesIO()
        final.name = "glitch.jpg"
        img.save_image(final)
        file_size = final.tell()
        final.seek(0)
    else:
        b = bytearray(b.getvalue())
        for x in range(sys.getsizeof(b)):
            if b[x] == 33 and b[x + 1] in [255, 249]:
                end = x
                break
        for x in range(13, end):
            b[x] = random.randint(0, 255)
        final = BytesIO(b)
        file_size = final.tell()
    file = discord.File(final, filename="glitch.jpeg")
    final.close()
    return (file, file_size)


def make_pixel(b: BytesIO, pixels: int) -> tuple[discord.File, int]:
    bg = (0, 0, 0)
    img = Image.open(b)
    img = img.resize((int(img.size[0] / pixels), int(img.size[1] / pixels)), Image.NEAREST)
    img = img.resize((int(img.size[0] * pixels), int(img.size[1] * pixels)), Image.NEAREST)
    load = img.load()
    for i in range(0, img.size[0], pixels):
        for j, r in itertools.product(range(0, img.size[1], pixels), range(pixels)):
            load[i + r, j] = bg
            load[i, j + r] = bg
    final = BytesIO()
    img.save(final, "png")
    file_size = final.tell()
    final.seek(0)
    file = discord.File(final, filename="pixelated.png")
    final.close()
    img.close()
    return (file, file_size)


def make_pixel_gif(b: Union[SupportsRead[bytes], bytes, Path, str], pixels, scale_msg):
    try:
        image = Image.open(b)
        gif_list = [frame.copy() for frame in ImageSequence.Iterator(image)]
    except OSError:
        return ":warning: Cannot load gif."
    bg = (0, 0, 0)
    img_list = []
    for frame in gif_list:
        img = Image.new("RGBA", frame.size)
        img.paste(frame, (0, 0))
        img = img.resize((int(img.size[0] / pixels), int(img.size[1] / pixels)), Image.NEAREST)
        img = img.resize((int(img.size[0] * pixels), int(img.size[1] * pixels)), Image.NEAREST)
        load = img.load()
        for i in range(0, img.size[0], pixels):
            for j, r in itertools.product(range(0, img.size[1], pixels), range(pixels)):
                load[i + r, j] = bg
                load[i, j + r] = bg
        img_list.append(img)
    final = BytesIO()
    img.save(final, format="GIF", save_all=True, append_images=img_list, duration=0, loop=0)
    file_size = final.tell()
    final.seek(0)
    file = discord.File(final, filename="pixelated.gif")
    final.close()
    img.close()
    return (file, file_size)


def do_waaw(b):  # sourcery skip: extract-method
    import numpy as np

    f = BytesIO()
    f2 = BytesIO()
    with wand.image.Image(file=b) as img:
        h1 = img.clone()
        width = int(img.width / 2) if int(img.width / 2) > 0 else 1
        h1.crop(width=width, height=int(img.height), gravity="east")
        h2 = h1.clone()
        h1.rotate(degree=180)
        h1.flip()
        h1.save(file=f)
        h2.save(file=f2)
    f.seek(0)
    f2.seek(0)
    list_im = [f2, f]
    imgs = [ImageOps.mirror(Image.open(i).convert("RGBA")) for i in list_im]
    min_shape = sorted([(np.sum(i.size), i.size) for i in imgs])[0][1]
    imgs_comb = np.hstack([np.asarray(i.resize(min_shape)) for i in imgs])
    imgs_comb = Image.fromarray(imgs_comb)
    final = BytesIO()
    imgs_comb.save(final, "png")
    file_size = final.tell()
    final.seek(0)
    file = discord.File(final, filename="waaw.png")
    f.close()
    f2.close()
    final.close()
    return (file, file_size)


def do_vw(b: Union[SupportsRead[bytes], bytes, Path, str], txt):
    im = Image.open(b)
    k = random.randint(0, 100)
    im = macintoshplus.draw_method1(k, txt, im)
    final = BytesIO()
    im.save(final, "png")
    file_size = final.tell()
    final.seek(0)
    file = discord.File(final, filename="vapewave.png")
    final.close()
    return (file, file_size)


def generate_ascii(image) -> tuple[BytesIO, int]:
    import aalib

    font = ImageFont.truetype(f"{str(BASE_DIR)}/FreeMonoBold.ttf", 15)
    (image_width, image_height) = image.size
    aalib_screen_width = int(image_width / 24.9) * 10
    aalib_screen_height = int(image_height / 41.39) * 10
    screen = aalib.AsciiScreen(width=aalib_screen_width, height=aalib_screen_height)

    im = image.convert("L").resize(screen.virtual_size)
    screen.put_image((0, 0), im)
    y = 0
    how_many_rows = len(screen.render().splitlines())
    (new_img_width, font_size) = font.getsize(screen.render().splitlines()[0])
    img = Image.new("RGBA", (new_img_width, how_many_rows * 15), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for lines in screen.render().splitlines():
        draw.text((0, y), lines, (0, 0, 0), font=font)
        y += 15
    ImageOps.fit(img, (image_width, image_height))

    final = BytesIO()
    img.save(final, "png")
    file_size = final.tell()
    final.seek(0)
    img.close()
    return (final, file_size)


def do_ascii(text):  # sourcery skip: extract-method
    from loguru import logger as log

    try:
        i = Image.new("RGB", (2000, 1000))
        img = ImageDraw.Draw(i)
        txt = figlet_format(text, font="starwars")
        img.text((20, 20), figlet_format(text, font="starwars"), fill=(0, 255, 0))
        (text_width, text_height) = img.textsize(figlet_format(text, font="starwars"))
        imgs = Image.new("RGB", (text_width + 30, text_height))
        ii = ImageDraw.Draw(imgs)
        ii.text((20, 20), figlet_format(text, font="starwars"), fill=(0, 255, 0))
        (text_width, text_height) = ii.textsize(figlet_format(text, font="starwars"))
        final = BytesIO()
        imgs.save(final, "png")
        file_size = final.tell()
        final.seek(0)
        file = discord.File(final, filename="ascii.png")
        final.close()
        imgs.close()
        return (file, txt, file_size)
    except Exception:
        log.exception("unable to make text")
        return (False, False)


def add_watermark(b, wmm, x, y, transparency, wm_gif: bool = False) -> tuple[bytes, str]:
    final = BytesIO()
    format = "png"
    with wand.image.Image(file=b) as img:
        is_gif = len(img.sequence) > 1
        if not is_gif and not wm_gif:
            log.debug("There are no gifs")
            with img.clone() as new_img:
                new_img.transform(resize="65536@")
                final_x = int(new_img.height * (x * 0.01))
                final_y = int(new_img.width * (y * 0.01))
                with wand.image.Image(file=wmm) as wm:
                    new_img.watermark(image=wm, left=final_x, top=final_y, transparency=transparency)
                new_img.save(file=final)

        elif is_gif and not wm_gif:
            format = "gif"
            log.debug("The base image is a gif")
            wm = wand.image.Image(file=wmm)
            with wand.image.Image() as new_image:
                with img.clone() as new_img:
                    for frame in new_img.sequence:
                        frame.transform(resize="65536@")
                        final_x = int(frame.height * (x * 0.01))
                        final_y = int(frame.width * (y * 0.01))
                        frame.watermark(image=wm, left=final_x, top=final_y, transparency=transparency)
                        new_image.sequence.append(frame)
                new_image.save(file=final)

        else:
            log.debug("The mark is a gif")
            format = "gif"
            with wand.image.Image() as new_image:
                with wand.image.Image(file=wmm) as new_img:
                    for frame in new_img.sequence:
                        with img.clone() as clone:
                            clone = clone.sequence[0] if is_gif else clone.convert("gif")
                            clone.transform(resize="65536@")
                            final_x = int(clone.height * (x * 0.01))
                            final_y = int(clone.width * (y * 0.01))
                            clone.watermark(image=frame, left=final_x, top=final_y, transparency=transparency)
                            new_image.sequence.append(clone)
                            new_image.dispose = "background"
                            with new_image.sequence[-1] as new_frame:
                                new_frame.delay = frame.delay

                new_image.save(file=final)

    return final.getvalue(), format
