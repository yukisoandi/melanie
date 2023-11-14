from __future__ import annotations

import itertools
import random as rand
from pathlib import Path
from typing import *
from typing import TYPE_CHECKING

from PIL import Image, ImageFilter
from typing_extensions import *

from . import constants, util

if TYPE_CHECKING:
    from _typeshed import SupportsRead


def edge(pixels, image: Union[SupportsRead[bytes], bytes, Path, str], angle: float):
    img = Image.open(image)
    img = img.rotate(angle, expand=True)
    edges = img.filter(ImageFilter.FIND_EDGES)
    edges = edges.convert("RGBA")
    edge_data = edges.load()

    filter_pixels = []
    edge_pixels = []
    intervals = []

    for y in range(img.size[1]):
        filter_pixels.append([])
        for x in range(img.size[0]):
            filter_pixels[y].append(edge_data[x, y])

    for y in range(len(pixels)):
        edge_pixels.append([])
        for x in range(len(pixels[0])):
            if util.lightness(filter_pixels[y][x]) < 0.25:
                edge_pixels[y].append(constants.white_pixel)
            else:
                edge_pixels[y].append(constants.black_pixel)

    for y, x in itertools.product(range(len(pixels) - 1, 1, -1), range(len(pixels[0]) - 1, 1, -1)):
        if edge_pixels[y][x] == constants.black_pixel and edge_pixels[y][x - 1] == constants.black_pixel:
            edge_pixels[y][x] = constants.white_pixel

    for y in range(len(pixels)):
        intervals.append([])
        for x in range(len(pixels[0])):
            if edge_pixels[y][x] == constants.black_pixel:
                intervals[y].append(x)
        intervals[y].append(len(pixels[0]))
    return intervals


def threshold(pixels, image, angle):
    intervals = []

    for y in range(len(pixels)):
        intervals.append([])
        for x in range(len(pixels[0])):
            if util.lightness(pixels[y][x]) < 0.25 or util.lightness(pixels[y][x]) > 0.8:
                intervals[y].append(x)
        intervals[y].append(len(pixels[0]))
    return intervals


def random(pixels, image, angle):
    intervals = []

    for y in range(len(pixels)):
        intervals.append([])
        x = 0
        while True:
            width = util.random_width(50)
            x += width
            if x > len(pixels[0]):
                intervals[y].append(len(pixels[0]))
                break
            else:
                intervals[y].append(x)
    return intervals


def waves(pixels, image, angle):
    intervals = []

    for y in range(len(pixels)):
        intervals.append([])
        x = 0
        while True:
            width = 50 + rand.randint(0, 10)
            x += width
            if x > len(pixels[0]):
                intervals[y].append(len(pixels[0]))
                break
            else:
                intervals[y].append(x)
    return intervals


def file_mask(pixels, image: Union[SupportsRead[bytes], bytes, Path, str], angle):
    intervals = []
    file_pixels = []

    img = Image.open(image)
    img = img.convert("RGBA")
    data = img.load()
    for y in range(img.size[1]):
        file_pixels.append([])
        for x in range(img.size[0]):
            file_pixels[y].append(data[x, y])

    for y, x in itertools.product(range(len(pixels) - 1, 1, -1), range(len(pixels[0]) - 1, 1, -1)):
        if file_pixels[y][x] == constants.black_pixel and file_pixels[y][x - 1] == constants.black_pixel:
            file_pixels[y][x] = constants.white_pixel

    for y in range(len(pixels)):
        intervals.append([])
        for x in range(len(pixels[0])):
            if file_pixels[y][x] == constants.black_pixel:
                intervals[y].append(x)
        intervals[y].append(len(pixels[0]))

    return intervals


def file_edges(pixels: Sized, image: Union[SupportsRead[bytes], bytes, Path, str], angle):
    img = Image.open(image)
    img = img.rotate(angle, expand=True)
    img = img.resize((len(pixels[0]), len(pixels)))
    edges = img.filter(ImageFilter.FIND_EDGES)
    edges = edges.convert("RGBA")
    edge_data = edges.load()

    filter_pixels = []
    edge_pixels = []
    intervals = []

    for y in range(img.size[1]):
        filter_pixels.append([])
        for x in range(img.size[0]):
            filter_pixels[y].append(edge_data[x, y])

    for y in range(len(pixels)):
        edge_pixels.append([])
        for x in range(len(pixels[0])):
            if util.lightness(filter_pixels[y][x]) < 0.25:
                edge_pixels[y].append(constants.white_pixel)
            else:
                edge_pixels[y].append(constants.black_pixel)

    for y, x in itertools.product(range(len(pixels) - 1, 1, -1), range(len(pixels[0]) - 1, 1, -1)):
        if edge_pixels[y][x] == constants.black_pixel and edge_pixels[y][x - 1] == constants.black_pixel:
            edge_pixels[y][x] = constants.white_pixel

    for y in range(len(pixels)):
        intervals.append([])
        for x in range(len(pixels[0])):
            if edge_pixels[y][x] == constants.black_pixel:
                intervals[y].append(x)
        intervals[y].append(len(pixels[0]))
    return intervals


def none(pixels: Sized, image, angle) -> list[list[int]]:
    return [[len(pixels[y])] for y in range(len(pixels))]
