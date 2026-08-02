"""The summary card: what a result means, in words and a handful of numbers.

A wall of curves is data, not guidance (STRUCTURE.md §6.8). For someone strong in CAD and
new to audio, the useful output is a short list of the numbers that characterise a design,
each computed **only from the trusted part of the curve** so nothing is quoted from a
region the model cannot represent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from freecad.audio_analysis.results.curve import ResponseCurve


@dataclass(frozen=True)
class ResponseSummary:
    """Scalar characterisation of one response curve."""

    label: str
    reference_spl: float
    #: Frequency the reference was taken at, or None when it is the passband median.
    reference_frequency: float | None
    peak_spl: float
    peak_frequency: float
    f3: float | None
    f10: float | None
    valid_below: float | None

    def format(self) -> str:
        lines = [f"{self.label}:"]
        where = (
            f"at {self.reference_frequency:.0f} Hz"
            if self.reference_frequency is not None
            else "passband median"
        )
        lines.append(f"  {self.reference_spl:.1f} dB reference ({where})")
        lines.append(f"  peak {self.peak_spl:.1f} dB at {self.peak_frequency:.0f} Hz "
                     f"({self.peak_spl - self.reference_spl:+.1f} dB)")
        if self.f3 is not None:
            lines.append(f"  -3 dB at {self.f3:.0f} Hz")
        else:
            lines.append("  -3 dB point not reached within the trusted range")
        if self.f10 is not None:
            lines.append(f"  -10 dB at {self.f10:.0f} Hz")
        if self.valid_below is not None:
            lines.append(f"  trustworthy below {self.valid_below:.0f} Hz")
        return "\n".join(lines)


def _crossing_below(curve: ResponseCurve, reference_db: float, drop: float) -> float | None:
    """Highest frequency below the reference where the response falls ``drop`` dB.

    Searches downward from the reference point, which is what "the -3 dB point" means for
    a bass roll-off. Returns None if the curve never falls that far.
    """
    spl = curve.spl
    target = reference_db - drop
    below = np.where(spl <= target)[0]
    if below.size == 0:
        return None
    index = int(below[-1])
    if index + 1 >= spl.size:
        return float(curve.frequency[index])
    # Linear interpolation in log-frequency between the two bracketing samples.
    f0, f1 = curve.frequency[index], curve.frequency[index + 1]
    s0, s1 = spl[index], spl[index + 1]
    if s1 == s0:
        return float(f0)
    fraction = (target - s0) / (s1 - s0)
    return float(10.0 ** (np.log10(f0) + fraction * (np.log10(f1) - np.log10(f0))))


def summarise_curve(
    curve: ResponseCurve, reference_frequency: float | None = None
) -> ResponseSummary:
    """Characterise a pressure curve.

    With no ``reference_frequency`` the reference is the **median SPL over the trusted
    range** -- a robust stand-in for the passband level. Anchoring instead on one end of
    the sweep would make "-3 dB point" meaningless whenever that end happens to sit on a
    slope, which for a headphone response it usually does.
    """
    if curve.quantity != "pressure":
        raise ValueError(f"can only summarise pressure curves, not {curve.quantity!r}")

    trusted = curve.trusted()
    spl = trusted.spl
    if reference_frequency is None:
        reference_spl = float(np.median(spl))
    else:
        reference_spl = trusted.spl_at(reference_frequency)
    peak = int(np.argmax(spl))

    return ResponseSummary(
        label=curve.label or "response",
        reference_spl=reference_spl,
        reference_frequency=reference_frequency,
        peak_spl=float(spl[peak]),
        peak_frequency=float(trusted.frequency[peak]),
        f3=_crossing_below(trusted, reference_spl, 3.0),
        f10=_crossing_below(trusted, reference_spl, 10.0),
        valid_below=curve.valid_below,
    )


def excursion_summary(curve: ResponseCurve, xmax: float) -> str:
    """How close the diaphragm comes to its excursion limit."""
    if curve.quantity != "displacement":
        raise ValueError(f"expected a displacement curve, not {curve.quantity!r}")
    trusted = curve.trusted()
    peak = float(np.max(trusted.magnitude))
    at = float(trusted.frequency[int(np.argmax(trusted.magnitude))])
    fraction = peak / xmax if xmax > 0 else float("inf")
    verdict = "within Xmax" if fraction <= 1.0 else "EXCEEDS Xmax"
    return (
        f"  peak excursion {peak * 1000:.3f} mm at {at:.0f} Hz "
        f"({fraction * 100:.0f}% of Xmax, {verdict})"
    )


def summarise_solution(solution: Any, analysis: Any = None) -> str:
    """A plain-language card for every driver and node in a solved network."""
    from freecad.audio_analysis.physics.network import Driver

    lines: list[str] = ["Summary:"]
    network = solution.network

    for node in network.node_names():
        label = node
        if analysis is not None:
            from freecad.audio_analysis.builder import label_for_node

            label = label_for_node(analysis, node)
        curve = solution.pressure(node)
        summary = summarise_curve(curve)
        lines.append(summary.format().replace(curve.label or "response", label, 1))

    for driver in network.drivers:
        lines.append(f"{driver.name}:")
        lines.append(excursion_summary(solution.excursion(driver.name), driver.parameters.Xmax))
        impedance = solution.input_impedance(driver.name).trusted()
        lines.append(
            f"  impedance {impedance.magnitude.min():.1f}-{impedance.magnitude.max():.1f} ohm"
        )

    return "\n".join(lines)
