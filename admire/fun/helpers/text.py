from __future__ import annotations

import unicodedata
from typing import Union

from melanie.curl import worker_download

from .constants import emoji_dict


def _(x):
    return x


def combine_emotes(img_url: str, format: str):  # sourcery skip: extract-method
    # Designed to be run in executor to avoid blocking
    img_bytes = worker_download(img_url)
    import cairosvg
    from wand.image import Image

    if format == "svg":
        kwargs = {"parent_width": 1024, "parent_height": 1024}
        return cairosvg.svg2png(bytestring=img_bytes, **kwargs)

    with Image(blob=img_bytes, format=format) as i:
        factor = 300 // i.width
        height2 = i.height * factor
        width2 = i.width * factor
        i.coalesce()
        i.optimize_layers()
        i.resize(width=width2, height=height2, filter="lanczos2sharp")
        return i.make_blob(format=format)


# used in ;react, checks if it's possible to react with the duper string or not
def has_dupe(duper: Union[str, list]) -> bool:
    collect_my_duper = [x for x in duper if x != "⃣"]
    #  ⃣ appears twice in the number unicode thing, so that must be stripped
    return len(set(collect_my_duper)) != len(collect_my_duper)


# used in ;react, replaces e.g. 'ng' with '🆖'
def replace_combos(react_me: str) -> str:
    for combo in emoji_dict["combination"]:
        if combo[0] in react_me:
            react_me = react_me.replace(combo[0], combo[1], 1)
    return react_me


# used in ;react, replaces e.g. 'aaaa' with '🇦🅰🍙🔼'
def replace_letters(react_me: str) -> str:
    for char in "abcdefghijklmnopqrstuvwxyz0123456789!?":
        char_count = react_me.count(char)
        if char_count > 1:  # there's a duplicate of this letter:
            if len(emoji_dict[char]) >= char_count:
                for i in range(char_count):
                    # moving goal post necessitates while loop instead of for
                    if emoji_dict[char][i] not in react_me:
                        react_me = react_me.replace(char, emoji_dict[char][i], 1)
                    else:
                        # skip this one because it's already been used by another replacement (e.g. circle emoji used to replace O already, then want to replace 0)
                        char_count += 1
        elif char_count == 1:
            react_me = react_me.replace(char, emoji_dict[char][0])
    return react_me


def extract_url_format(emoji):
    if emoji[0] == "<":
        name = emoji.split(":")[1]
        emoji_name = emoji.split(":")[2][:-1]
        if emoji.split(":")[0] == "<a":
            # animated custom emoji
            url = f"https://cdn.discordapp.com/emojis/{emoji_name}.gif"
            name += ".gif"
            format = "gif"
        else:
            url = f"https://cdn.discordapp.com/emojis/{emoji_name}.png"
            name += ".png"
            format = "png"
    else:
        chars = []
        name = []
        for char in emoji:
            chars.append(hex(ord(char))[2:])
            try:
                name.append(unicodedata.name(char))
            except ValueError:
                # Sometimes occurs when the unicodedata library cannot
                # resolve the name, however the image still exists
                name.append("none")
        name = "_".join(name) + ".png"

        if len(chars) == 2 and "fe0f" in chars:
            # remove variation-selector-16 so that the appropriate url can be built without it
            chars.remove("fe0f")
        if "20e3" in chars:
            # COMBINING ENCLOSING KEYCAP doesn't want to play nice either
            chars.remove("fe0f")

        url = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/svg/" + "-".join(chars) + ".svg"

        format = "svg"

    return (url, format, name)
