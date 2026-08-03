"""Target curves: how far a design is from where it is meant to be.

A response on its own says what a headphone does. Against a target it says what is *wrong*
with it, which is the form a design decision can be made from — and for headphones the
target is the whole argument, because there is no room, no reflections and no reference
except an agreed curve.

**Targets are loaded, never shipped.** Harman, Diffuse Field and the rest are published
research but not redistributable data (STRUCTURE.md §6.4), so this module reads whatever
file the user has and never bundles one.

**Comparison is of shape, not level.** A target curve is defined up to an arbitrary offset
— it says a headphone should have 6 dB more bass than treble, not that it should produce
94 dB. So the measured curve is offset to match the target over the comparison band before
the difference is taken. Reporting an absolute difference instead would make the answer
depend on the drive voltage, which has nothing to do with whether the tuning is right.

**And the band is stated.** A deviation figure without a band is meaningless: nearly every
headphone matches nearly every target over a narrow enough range, and a lumped model has a
validity limit well below the top of the audio band, so the comparison is clipped to the
part of the curve that can be believed.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from freecad.audio_analysis.results.curve import ResponseCurve


class TargetError(ValueError):
    """Raised when a target curve cannot be read or compared."""


#: Column headings that plausibly hold a level in decibels, lowercased.
_LEVEL_HEADINGS = ("spl", "db", "level", "magnitude", "amplitude", "target")
_FREQUENCY_HEADINGS = ("freq", "hz", "frequency")


@dataclass(frozen=True)
class TargetCurve:
    """A level-versus-frequency target, in decibels on an arbitrary reference."""

    frequency: np.ndarray
    level_db: np.ndarray
    label: str = "target"
    source: str = ""

    def __post_init__(self) -> None:
        frequency = np.asarray(self.frequency, dtype=float)
        level = np.asarray(self.level_db, dtype=float)
        if frequency.shape != level.shape:
            raise TargetError(
                f"the target has {frequency.size} frequencies and {level.size} levels"
            )
        if frequency.size < 2:
            raise TargetError("a target curve needs at least two points")
        order = np.argsort(frequency)
        frequency, level = frequency[order], level[order]
        if np.any(frequency <= 0.0):
            raise TargetError("target frequencies must be positive")
        object.__setattr__(self, "frequency", frequency)
        object.__setattr__(self, "level_db", level)

    @property
    def range(self) -> tuple[float, float]:
        return float(self.frequency[0]), float(self.frequency[-1])

    def at(self, frequency: np.ndarray) -> np.ndarray:
        """Level at arbitrary frequencies, interpolated on a log axis.

        Log because a target is defined per octave; linear interpolation across a decade
        of a sparse target would invent a shape the author did not write.
        """
        return np.interp(
            np.log10(np.asarray(frequency, dtype=float)),
            np.log10(self.frequency),
            self.level_db,
        )


def _split(line: str) -> list[str]:
    return [part for part in line.replace(",", " ").split() if part]


def load_target(path: str, label: str = "") -> TargetCurve:
    """Read a target from CSV or FRD.

    Both are plain text with a frequency in the first column, so one reader serves. A
    header line naming the columns is used when present and guessed past when not: FRD has
    no header, and a CSV exported from a measurement tool usually does.
    """
    if not os.path.isfile(path):
        raise TargetError(f"no such file: {path}")

    frequencies: list[float] = []
    levels: list[float] = []
    level_column = 1

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped[0] in "*#;":
                continue
            parts = _split(stripped)
            if len(parts) < 2:
                continue
            try:
                frequency = float(parts[0])
            except ValueError:
                # A header row. Pick the column whose name looks like a level.
                lowered = [p.lower() for p in parts]
                if not any(h in lowered[0] for h in _FREQUENCY_HEADINGS):
                    continue
                for index, name in enumerate(lowered[1:], start=1):
                    if any(heading in name for heading in _LEVEL_HEADINGS):
                        level_column = index
                        break
                continue
            if level_column >= len(parts):
                continue
            try:
                levels.append(float(parts[level_column]))
            except ValueError:
                continue
            frequencies.append(frequency)

    if len(frequencies) < 2:
        raise TargetError(
            f"{os.path.basename(path)} yielded {len(frequencies)} usable point(s). "
            f"Expected two columns of numbers -- frequency in Hz, then level in dB -- as "
            f"CSV or FRD."
        )
    return TargetCurve(
        np.array(frequencies), np.array(levels),
        label=label or os.path.splitext(os.path.basename(path))[0],
        source=path,
    )


@dataclass(frozen=True)
class Deviation:
    """How far a response sits from a target, and over what."""

    frequency: np.ndarray
    #: Measured minus target, in dB, after offsetting to match.
    difference_db: np.ndarray
    band: tuple[float, float]
    offset_db: float
    target: TargetCurve

    @property
    def rms(self) -> float:
        return float(np.sqrt(np.mean(self.difference_db**2)))

    @property
    def worst(self) -> tuple[float, float]:
        """``(frequency, difference)`` at the largest departure."""
        index = int(np.argmax(np.abs(self.difference_db)))
        return float(self.frequency[index]), float(self.difference_db[index])

    def format(self) -> str:
        where, amount = self.worst
        direction = "too much" if amount > 0 else "too little"
        return (
            f"vs {self.target.label}:\n"
            f"  {self.rms:.2f} dB RMS over {self.band[0]:.0f}-{self.band[1]:.0f} Hz\n"
            f"  worst {amount:+.1f} dB at {where:.0f} Hz ({direction} there)\n"
            f"  level-matched by {self.offset_db:+.1f} dB, since a target fixes shape "
            f"rather than absolute level"
        )


def compare(
    curve: ResponseCurve,
    target: TargetCurve,
    band: Sequence[float] | None = None,
) -> Deviation:
    """Difference between a pressure response and a target, over their common band.

    The band defaults to the overlap of three things: the curve's own frequencies, the
    target's, and the curve's validity limit. Each of those is a real boundary, and
    silently extrapolating past any of them would produce a deviation figure about a
    region nobody has information on.
    """
    if curve.quantity != "pressure":
        raise TargetError(f"a target compares against pressure, not {curve.quantity!r}")

    low = max(curve.frequency.min(), target.frequency.min())
    high = min(curve.frequency.max(), target.frequency.max())
    if curve.valid_below is not None:
        high = min(high, curve.valid_below)
    if band is not None:
        low, high = max(low, float(band[0])), min(high, float(band[1]))
    if not high > low:
        raise TargetError(
            f"no overlap to compare over: the response covers "
            f"{curve.frequency.min():.0f}-{curve.frequency.max():.0f} Hz"
            + (f" (valid to {curve.valid_below:.0f} Hz)" if curve.valid_below else "")
            + f" and the target covers {target.range[0]:.0f}-{target.range[1]:.0f} Hz."
        )

    keep = (curve.frequency >= low) & (curve.frequency <= high)
    frequency = curve.frequency[keep]
    measured = curve.spl[keep]
    wanted = target.at(frequency)

    # Match the levels before comparing shapes. A mean in dB weighted by nothing is the
    # right choice on a log frequency axis with logarithmically spaced samples, which is
    # what log_frequencies produces.
    offset = float(np.mean(wanted - measured))
    return Deviation(
        frequency=frequency,
        difference_db=(measured + offset) - wanted,
        band=(float(frequency[0]), float(frequency[-1])),
        offset_db=offset,
        target=target,
    )
