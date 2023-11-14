from __future__ import annotations

import io
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Optional

import arrow
import discord
import discord.context_managers
import filetype
import regex as re
import yt_dlp
from boltons.urlutils import URL as BURL
from boltons.urlutils import find_all_links
from loguru import logger as log
from xxhash import xxh32_hexdigest

from melanie import URL, bytes2human, footer_gif, hex_to_int, intword
from runtimeopt import offloaded
from videofetch.core import (
    REDDIT_HEX,
    TWITTER_HEX,
    YOUTUBE_HEX,
    DiscordResult,
    VideoDownload,
)

REDGIF_HEX = "#B31AA5"
PORNHUB_HEX = "#FD9900"

MEDAL_HEX = "#543DA7"

TWITCH_HEX = "#9146FE"


@contextmanager
def get_cookiefile():
    import io
    import os
    from contextlib import ExitStack

    from cryptography.fernet import Fernet
    from redis.client import Redis

    stack = ExitStack()
    redis = Redis(single_connection_client=True)

    fernet = Fernet(os.environ["FERNET_KEY"])

    if _cookies := redis.get("encrypted_cookies"):
        _cookies = _cookies.decode("UTF-8")

        cookiedata = fernet.decrypt(_cookies)

        tmpfile = io.StringIO(cookiedata.decode("UTF-8"))

    else:
        tmpfile = None
    try:
        yield tmpfile
    finally:
        redis.close()
        stack.close()


@offloaded
def download_video(url: str, task_id: str):
    import subprocess

    import distributed
    from distributed import Event
    from loguru import logger as log
    from xxhash import xxh3_64_hexdigest

    from melanie import borrow_temp_file_sync
    from videofetch.core import VideoDownload
    from videofetch.h264_conv import get_video_encoding
    from videofetch.helpers import get_cookiefile

    cache_dir = Path("videofetch_cache")
    if not cache_dir.exists():
        msg = "Cache invalid"
        raise ValueError(msg)

    final_file = cache_dir / f"{xxh3_64_hexdigest(url)}.pkl"
    client = distributed.get_client()

    lock = distributed.Lock(f"downloads:{task_id}", client=client)
    event = Event(name=task_id, client=client)
    with lock:
        if final_file.exists():
            log.success("{} is cached. Returning immediately", final_file)
            return final_file.read_bytes()
        with get_cookiefile() as cookies:
            YDL_OPTS = {
                "cookiefile": cookies,
                "age_limit": 18,
                "outtmpl": str(cache_dir) + "/%(id)s_%(title)s.%(ext)s",
                "cachedir": str(cache_dir) + "/yt_dlp_cache",
                "concurrent_fragment_downloads": 12,
                "extract_flat": "discard_in_playlist",
                "final_ext": "mp4",
                "format": "bv*[filesize<100M][height<=?720][ext=mp4]+ba[ext=m4a]/b[height<=?720]",
                "fragment_retries": 10,
                "http_chunk_size": 1048576,
                "noplaylist": True,
                "restrictfilenames": True,
                "retries": 0,
                "break_per_url": True,
                "windowsfilenames": True,
            }

            if "youtube" in url:
                YDL_OPTS["format"] = "bv*[filesize<100M][height<=?720][ext=mp4]+ba[ext=m4a]/b[height<=?720]"

            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(url, download=False)
                with suppress(KeyError):
                    if info["is_live"]:
                        return "TIME"
                duration = int(info.get("duration", 0))
                int(info.get("age_limit", 0))
                event.set()
                info = ydl.process_video_result(info, download=True)
                video = VideoDownload.parse_obj(info)
                if not video.duration:
                    video.duration = 0
                vid = Path(video.requested_downloads[0].filepath)
                if not vid.exists():
                    raise ValueError("No DL")
                try:
                    video.video_bytes = vid.read_bytes()
                    test_result = get_video_encoding(str(vid))
                    video_size = len(video.video_bytes)
                    if video_size >= 94371840 or test_result.is_h264() is not True:
                        unit = video_size / duration
                        target_dur = int(94371840 / unit)
                        _codec = "copy" if test_result.is_h264() else "libopenh264"
                        args = [
                            "ffmpeg",
                            "-i",
                            str(vid),
                            "-c:a",
                            "copy",
                            "-c:v",
                            _codec,
                            "-t",
                            str(target_dur),
                            "-movflags",
                            "+faststart",
                        ]
                        if _codec != "copy":
                            args.extend(
                                [
                                    "-filter:v",
                                    "scale='min(1920,iw)':min'(1080,ih)',fps=30",
                                    "-allow_skip_frames",
                                    "1",
                                    "-profile:v",
                                    "main",
                                ],
                            )

                        with borrow_temp_file_sync(extension=".mp4", base=str(cache_dir)) as outfile2:
                            args.extend(["-y", str(outfile2)])
                            try:
                                subprocess.check_output(args, timeout=60)
                            except subprocess.TimeoutExpired:
                                log.error("Timeout expired for {}", url)
                                return None
                            video.video_bytes = outfile2.read_bytes()
                    final_data = video.to_bytes()
                    final_file.write_bytes(final_data)
                    return final_data
                finally:
                    vid.unlink(missing_ok=True)


def check_message(content: str) -> Optional[URL]:
    links = find_all_links(content)
    if not links:
        return None

    ok_domains = ("twitter", "youtube", "youtu", "pornhub", "redgif", "hanime", "xvideos", "youporn", "xnxx", "twitch")

    link: BURL = links[0]

    for domain in ok_domains:
        if domain in link.host:
            url = str(link)
            break

    url: URL = next((x for x in links if any(base_host in x.host for base_host in ok_domains)), None)

    if not url:
        return None
    if "twitter" in url.host:
        return url if "status" in str(url) else None
    if "reddit" in url.host and "/r/" in str(url):
        return url

    if "pornhub" in str(url):
        return url
    if "youtu" in str(url):
        return url
    if "hanime" in str(url):
        return url
    if "redgif" in str(url):
        return url

    if "clip" in str(url):
        return url

    if "youporn" in str(url):
        return url

    if "xnxx" in str(url):
        return url

    if "medal.tv" in str(url):
        return url

    return url if "xvideos" in str(url) else None


async def make_discord_result(dl: VideoDownload, requester: discord.User) -> DiscordResult:
    # sourcery skip: extract-duplicate-method
    em = discord.Embed()
    desc = ""
    try:
        if dl.like_count and dl.like_count > 0:
            em.set_footer(text=f"\n 👍   {intword(dl.like_count)}", icon_url=footer_gif)
    except Exception:
        log.exception("Unable to set footer")
        em.set_footer(text="melanie ^_^", icon_url=footer_gif)
    # Reddit

    if "reddit" in dl.webpage_url_domain:
        try:
            subreddit = re.findall(r"/r/([^\s/]+)", dl.original_url)[0]
            author_name = f"{dl.uploader} r/{subreddit}"
        except Exception:
            log.exception(f"Unable to get the subreddit from {dl.original_url}")
            author_name = dl.uploader
        em.color = hex_to_int(REDDIT_HEX)
        desc = f"[Reddit]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=author_name, icon_url="https://cdn.discordapp.com/attachments/918929359161663498/975512554795311124/reddit-logo-16.png")

    elif "twitter" in dl.webpage_url_domain:
        em.color = hex_to_int(TWITTER_HEX)
        desc = f"[Tweet]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=dl.uploader, icon_url="https://cdn.discordapp.com/attachments/918929359161663498/975512829606121513/580b57fcd9996e24bc43c53e_1.png")

    elif "twitch" in dl.webpage_url_domain:
        em.color = hex_to_int(TWITCH_HEX)
        desc = f"[Twitch]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=dl.uploader, icon_url="https://cdn.discordapp.com/attachments/928400431137296425/984252724617023529/twitch.png")

    elif "youtu" in dl.webpage_url_domain:
        em.color = hex_to_int(YOUTUBE_HEX)
        desc = f"[YouTube]({dl.original_url}) requested by {requester.mention}"
        em.set_author(
            name=dl.uploader,
            icon_url="https://cdn.discordapp.com/attachments/918929359161663498/975512142432333824/free-youtube-logo-icon-2431-thumb_1.png",
        )

    elif "redgif" in dl.webpage_url_domain:
        em.color = hex_to_int(REDDIT_HEX)
        desc = f"[RedGif]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=dl.uploader, icon_url="https://cdn.discordapp.com/attachments/918929359161663498/970126366735486976/communityIcon_iyj9exc4x3041.png")

    elif "pornhub" in dl.webpage_url_domain:
        em.color = hex_to_int(PORNHUB_HEX)
        desc = f"[PornHub]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=dl.uploader)

    elif "medal" in dl.webpage_url_domain:
        em.color = hex_to_int(MEDAL_HEX)
        desc = f"[Medal]({dl.original_url}) requested by {requester.mention}"
        em.set_author(name=dl.uploader, icon_url="https://cdn.discordapp.com/attachments/918929359161663498/975505035670724758/medal-with-bg.png")

    desc += f"\n{dl.title}"

    em.description = desc
    em.description = em.description.replace("recorded with Medal.tv", "")
    if dl.upload_date:
        try:
            em.timestamp = arrow.get(dl.upload_date).datetime
        except Exception:
            log.exception("Unable to set timestamp")

    extension = filetype.guess_extension(dl.video_bytes)
    if not extension:
        msg = "No ext"
        raise ValueError(msg)
    name = f"melanieVideo_{xxh32_hexdigest(dl.video_bytes)}.{extension}"
    file = discord.File(io.BytesIO(dl.video_bytes), filename=name)
    final = DiscordResult(file=file, embed=em, file_size=len(dl.video_bytes))
    log.success(f"Returning final result of {bytes2human(len(dl.video_bytes))}  for file {name}")
    return final
