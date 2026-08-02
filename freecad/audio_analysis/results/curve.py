"""Frequency-response curves — the workbench's primary output.

Almost everything a user looks at is a complex quantity sampled over frequency: sound
pressure at a probe, electrical impedance at the terminals, diaphragm excursion. One
container serves them all, so plotting, smoothing, export and comparison are written once.

Two design choices carry weight:

**Values are complex, always.** Storing magnitude only would be simpler and would quietly
destroy the workbench's ability to do its job. Multiple drivers sum with *phase*
(STRUCTURE.md §2.4); reversing one driver's polarity changes the summed response by tens
of dB, and a magnitude-only pipeline cannot represent that. Group delay is a phase
derivative. Impedance phase decides amplifier loading.

**Curves know where they stop being true.** A lumped result is valid only below the
frequency where its cavity stops behaving as a compliance (§2.4). Carrying that limit on
the curve itself means every plot, export and summary can mark it, rather than each
consumer having to remember.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

import numpy as np

from freecad.audio_analysis.physics import air


@dataclass(frozen=True)
class ResponseCurve:
    """A complex quantity sampled over frequency."""

    #: Frequencies in Hz, strictly increasing.
    frequency: np.ndarray
    #: Complex values, same length as ``frequency``.
    values: np.ndarray
    #: What the values are: "pressure", "impedance", "displacement", "velocity".
    quantity: str = "pressure"
    #: SI unit of the values, for axis labels and export headers.
    unit: str = "Pa"
    #: Name shown in legends.
    label: str = ""
    #: Frequency above which this curve should not be trusted, if any (§2.4).
    valid_below: float | None = None
    #: Provenance: solver, mesh size, drive level, date. Written onto exports and plots.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        frequency = np.asarray(self.frequency, dtype=float)
        values = np.asarray(self.values, dtype=complex)
        if frequency.ndim != 1:
            raise ValueError("frequency must be one-dimensional")
        if frequency.shape != values.shape:
            raise ValueError(
                f"frequency and values must be the same length, got "
                f"{frequency.shape[0]} and {values.shape[0]}"
            )
        if frequency.size == 0:
            raise ValueError("a curve needs at least one frequency point")
        if np.any(frequency <= 0.0):
            raise ValueError("frequencies must be positive")
        if np.any(np.diff(frequency) <= 0.0):
            raise ValueError("frequencies must be strictly increasing")
        object.__setattr__(self, "frequency", frequency)
        object.__setattr__(self, "values", values)

    # -- derived quantities ----------------------------------------------------------

    @property
    def magnitude(self) -> np.ndarray:
        return np.abs(self.values)

    @property
    def phase_rad(self) -> np.ndarray:
        """Unwrapped phase in radians -- unwrapped so group delay is meaningful."""
        return np.unwrap(np.angle(self.values))

    @property
    def phase_deg(self) -> np.ndarray:
        return np.degrees(self.phase_rad)

    @property
    def spl(self) -> np.ndarray:
        """Sound pressure level in dB re 20 uPa.

        Only meaningful for a pressure curve; raises otherwise rather than returning a
        number that looks like an SPL but is a level of something else.
        """
        if self.quantity != "pressure":
            raise ValueError(f"SPL is only defined for pressure, not {self.quantity!r}")
        # Guard the log against exact zeros, which occur at perfect cancellation.
        magnitude = np.maximum(self.magnitude, np.finfo(float).tiny)
        return 20.0 * np.log10(magnitude / air.P_REF)

    @property
    def group_delay(self) -> np.ndarray:
        """Group delay in seconds, ``-dphi/domega``.

        Computed with a central difference, so the two end points are one-sided and
        slightly less accurate. Audible as timing smear; large excursions near a
        crossover usually mean the drivers are fighting each other.
        """
        if self.frequency.size < 2:
            return np.zeros_like(self.frequency)
        omega = 2.0 * math.pi * self.frequency
        return -np.gradient(self.phase_rad, omega)

    # -- operations ------------------------------------------------------------------

    def smooth(self, fraction: int = 6) -> "ResponseCurve":
        """Fractional-octave smoothing, e.g. ``fraction=6`` for 1/6-octave.

        Smooths **magnitude** and leaves phase alone, which is the convention in audio
        measurement: smoothing complex values would average away real cancellation
        notches and misrepresent the summation the curve exists to show.
        """
        if fraction <= 0:
            raise ValueError(f"fraction must be positive, got {fraction}")

        half_width = 2.0 ** (1.0 / (2.0 * fraction))
        magnitude = self.magnitude
        smoothed = np.empty_like(magnitude)
        for i, f in enumerate(self.frequency):
            lo, hi = f / half_width, f * half_width
            window = (self.frequency >= lo) & (self.frequency <= hi)
            smoothed[i] = magnitude[window].mean() if window.any() else magnitude[i]

        phase = self.phase_rad
        return replace(
            self,
            values=smoothed * np.exp(1j * phase),
            label=f"{self.label} (1/{fraction} oct)" if self.label else self.label,
        )

    def at(self, frequency: float) -> complex:
        """Value at one frequency, log-interpolated between samples."""
        if frequency <= 0.0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        log_f = np.log10(self.frequency)
        target = np.log10(frequency)
        real = np.interp(target, log_f, self.values.real)
        imag = np.interp(target, log_f, self.values.imag)
        return complex(real, imag)

    def spl_at(self, frequency: float) -> float:
        """SPL in dB at one frequency."""
        if self.quantity != "pressure":
            raise ValueError(f"SPL is only defined for pressure, not {self.quantity!r}")
        return air.pressure_to_spl(max(abs(self.at(frequency)), np.finfo(float).tiny))

    def trusted(self) -> "ResponseCurve":
        """The portion of the curve below its validity limit.

        Plots draw the whole curve and grey the remainder; summary metrics use this, so a
        number like "-3 dB point" is never quoted from a region the model cannot model.
        """
        if self.valid_below is None:
            return self
        keep = self.frequency <= self.valid_below
        if not keep.any():
            raise ValueError(
                f"no part of {self.label or 'this curve'} lies below its validity "
                f"limit of {self.valid_below:.0f} Hz"
            )
        return replace(self, frequency=self.frequency[keep], values=self.values[keep])

    # -- combination -----------------------------------------------------------------

    @staticmethod
    def sum(curves: Sequence["ResponseCurve"], label: str = "Sum") -> "ResponseCurve":
        """Complex sum of several curves, sharing one frequency axis.

        This is how multiple drivers combine at a listening point. It is a *complex* sum,
        so relative phase decides reinforcement or cancellation -- summing magnitudes
        instead would make crossover work impossible (§2.4).

        The result inherits the most restrictive validity limit, since a sum is only as
        trustworthy as its least trustworthy contributor.
        """
        if not curves:
            raise ValueError("nothing to sum")
        first = curves[0]
        for curve in curves[1:]:
            if not np.array_equal(curve.frequency, first.frequency):
                raise ValueError("curves must share the same frequency axis to be summed")
            if curve.quantity != first.quantity:
                raise ValueError(
                    f"cannot sum {first.quantity!r} and {curve.quantity!r} curves"
                )

        limits = [c.valid_below for c in curves if c.valid_below is not None]
        return replace(
            first,
            values=np.sum([c.values for c in curves], axis=0),
            label=label,
            valid_below=min(limits) if limits else None,
        )

    def inverted(self) -> "ResponseCurve":
        """The same curve with reversed polarity.

        Flipping a driver's wiring is a real design action with a large effect through a
        crossover region, so it deserves to be one call.
        """
        return replace(
            self, values=-self.values, label=f"{self.label} (inverted)" if self.label else ""
        )

    # -- export ----------------------------------------------------------------------

    def to_csv(self, path: str) -> None:
        """Write frequency, magnitude, phase and (for pressure) SPL."""
        header = [f"# {self.label or self.quantity}"]
        header += [f"# {k}: {v}" for k, v in sorted(self.metadata.items())]
        if self.valid_below is not None:
            header.append(f"# valid below: {self.valid_below:.1f} Hz")

        columns = ["frequency_Hz", f"magnitude_{self.unit}", "phase_deg"]
        data = [self.frequency, self.magnitude, self.phase_deg]
        if self.quantity == "pressure":
            columns.append("SPL_dB")
            data.append(self.spl)
        header.append(",".join(columns))

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(header) + "\n")
            for row in zip(*data):
                handle.write(",".join(f"{v:.6g}" for v in row) + "\n")

    def to_frd(self, path: str) -> None:
        """Write an FRD file: frequency, SPL, phase.

        The plain-text convention understood by most loudspeaker design tools, so results
        can leave this workbench for a crossover simulator or an enclosure tool.
        """
        if self.quantity != "pressure":
            raise ValueError(f"FRD holds pressure responses, not {self.quantity!r}")
        spl, phase = self.spl, self.phase_deg
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"* {self.label or 'response'}\n")
            for k, v in sorted(self.metadata.items()):
                handle.write(f"* {k}: {v}\n")
            for f, s, p in zip(self.frequency, spl, phase):
                handle.write(f"{f:.4f} {s:.4f} {p:.4f}\n")


def log_frequencies(start: float, stop: float, points_per_octave: int = 24) -> np.ndarray:
    """Logarithmically spaced frequencies -- the natural axis for audio.

    Constant resolution per octave, so the bass is sampled as finely as the treble
    relative to what the ear does. A linear sweep wastes most of its points above 10 kHz.
    """
    if start <= 0.0 or stop <= start:
        raise ValueError(f"need 0 < start < stop, got {start} and {stop}")
    if points_per_octave <= 0:
        raise ValueError("points_per_octave must be positive")
    octaves = math.log2(stop / start)
    count = max(2, int(round(octaves * points_per_octave)) + 1)
    return start * 2.0 ** np.linspace(0.0, octaves, count)
