from __future__ import annotations

from string import ascii_uppercase

__all__ = ["bitrate", "filesize"]
__author__ = "Dave <orangechannel@pm.me>"
__date__ = "30 November 2019"


def bitrate(size: str, seconds: int = None, frames: int = None, framerate: float = 24000 / 1001):
    """Converts a desired filesize into average bitrate in kbps.

    >>> bitrate('118.5 MB', seconds=1050)  # 118.5 megabytes
    bitrate should be 903 kbps
    >>> bitrate('24 g', frames=46500)  # 24 gigabits
    bitrate should be 12,375 kbps
    >>> bitrate('24 gi', frames=46500)  # 24 gibibits
    bitrate should be 13,287 kbps

    :param size: desired size i.e. '4.7 GiB'
        size is in the format '<float> <unit>' where unit can take any
        of the following forms:
        T=TB,   Ti=TiB,   t=tb,   ti=tib
        G=GB,   Gi=GiB,   g=gb,   gi=gib
        M=MB,   Mi=MiB,   m=mb,   mi=mib
        K=KB,   Ki=KiB,   k=kb,   ki=kib

        an 'i' indicates a binary system (2**30 = GiB)
        otherwise uses a decimal system (1E9 = GB)

        capital 'T, G, M, K' indicates the size is in bytes
        lowercase 't, g, m, k' indicates the size is in bits

    :param seconds: number of seconds in clip (Default value = None)
    :param frames: number of frames in clip (Default value = None)
    :param framerate: clip fps used with `frames`
                      (Default value = 23.976)

    """
    if not seconds and not frames:
        msg = "find_bitrate: either seconds or frames must be specified"
        raise ValueError(msg)
    if frames:
        seconds = frames / framerate

    number, prefix = size.split()
    number = float(number)

    size = number * 8 if prefix[0] in ascii_uppercase else number

    if "i" in prefix:
        ter = 2**40
        gig = 2**30
        meg = 2**20
        kil = 2**10
        conv = 2**10 / 1e3
    else:
        ter = 1e12
        gig = 1e9
        meg = 1e6
        kil = 1e3
        conv = 1

    if prefix[0] in ["T", "t"]:
        size = size * (ter / kil) * conv
    if prefix[0] in ["G", "g"]:
        size = size * (gig / kil) * conv
    elif prefix[0] in ["M", "m"]:
        size = size * (meg / kil) * conv
    elif prefix[0] in ["K", "k"]:
        size = size * conv
    else:
        msg = "find_bitrate: size unit is unexpected"
        raise ValueError(msg)

    return round(size / seconds, 2)


def filesize(brate: int, seconds: int = None, frames: int = None, framerate: float = 24000 / 1001):
    """Estimates filesize based on average bitrate in kbps.

    >>> filesize(4800, seconds=60*24)  # 4,800 kbps for 24 minutes
    estimated filesize is 823.97 MiB or 864.00 MB
    >>> filesize(8710, frames=840)  # 8,710 kbps for 840 frames
    estimated filesize is 36.38 MiB or 38.14 MB
    >>> filesize(12375, frames=46500)  # 12,375 kbps for 46500 frames
    estimated filesize is 2.79 GiB or 3.00 GB

    :param brate: must be specified in kilobits per second (kbps)
    :param seconds: number of seconds in clip (Default value = None)
    :param frames: number of frames in clip (Default value = None)
    :param framerate: clip fps used with `frames`
                      (Default value = 23.976)

    """
    if not seconds and not frames:
        msg = "find_filesize: either seconds or frames must be specified"
        raise ValueError(msg)

    if frames:
        seconds = frames / framerate

    size = brate * 1000 * seconds
    size /= 8

    if size > 2**40:
        size / 2**40
    elif size > 2**30:
        size / 2**30
    elif size > 2**20:
        size / 2**20
    elif size > 2**10:
        size / 2**10
    else:
        msg = "find_filesize: resulting size too small"
        raise ValueError(msg)

    if size > 1e12:
        size / 1e12
        decimal = "T"
    elif size > 1e9:
        size / 1e9
        decimal = "G"
    elif size > 1e6:
        size / 1e6
        decimal = "M"
    elif size > 1e3:
        size / 1e3
        decimal = "K"
    else:
        msg = "find_filesize: resulting size too small"
        raise ValueError(msg)

    return float(decimal)


find_bitrate = bitrate
find_filesize = filesize
