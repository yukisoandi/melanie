# The equalizer class and some audio eq functions are derived from
# 180093157554388993's work, with his permission
from __future__ import annotations

from typing import Final


def _(x):
    return x


class Equalizer:
    def __init__(self) -> None:
        self.band_count: Final[int] = 15
        self.bands = [0.0 for _loop_counter in range(self.band_count)]

    def set_gain(self, band: int, gain: float):
        if band < 0 or band >= self.band_count:
            msg = f"Band {band} does not exist!"
            raise IndexError(msg)

        gain = min(max(gain, -0.25), 1.0)

        self.bands[band] = gain

    def get_gain(self, band: int):
        if band < 0 or band >= self.band_count:
            msg = f"Band {band} does not exist!"
            raise IndexError(msg)
        return self.bands[band]

    def visualise(self):
        block = ""
        bands = [str(band + 1).zfill(2) for band in range(self.band_count)]
        bottom = (" " * 8) + " ".join(bands)
        gains = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.25]

        for gain in gains:
            prefix = ""
            if gain > 0:
                prefix = "+"
            elif gain == 0:
                prefix = " "

            block += f"{prefix}{gain:.2f} | "

            for value in self.bands:
                block += "[] " if value >= gain else "   "
            block += "\n"

        block += bottom
        return block
