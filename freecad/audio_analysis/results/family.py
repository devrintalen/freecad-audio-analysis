"""Families of curves — what a *decision* does, rather than what a design does.

One curve tells you how a design behaves. Two tell you what changing something achieves,
and that is the difference between a plotting tool and a design tool (STRUCTURE.md §6.9).
The open-back question that started this project — how large should the rear vents be, how
much should they be damped — is not answered by one response; it is answered by five
responses side by side and the delta between them.

A family shares one frequency axis and one observed quantity, so it can be overlaid
directly. It also carries a **reference member**, because the useful view is usually not
the curves themselves but their difference from a baseline: a 2 dB change buried in a 40 dB
roll-off is invisible on an absolute plot and obvious on a delta plot.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Sequence

import numpy as np

from freecad.audio_analysis.results.curve import ResponseCurve


@dataclass(frozen=True)
class CurveFamily:
    """Several curves of the same quantity, differing by one parameter."""

    #: What was varied, e.g. "RearVent.Area".
    parameter: str
    #: One label per curve, e.g. "4 cm^2". Same length as :attr:`curves`.
    labels: list[str]
    curves: list[ResponseCurve]
    #: Index of the curve deltas are measured against, or None for no baseline.
    reference: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.curves:
            raise ValueError("a family needs at least one curve")
        if len(self.labels) != len(self.curves):
            raise ValueError(
                f"{len(self.labels)} labels for {len(self.curves)} curves; they must match"
            )
        first = self.curves[0]
        for curve in self.curves[1:]:
            if not np.array_equal(curve.frequency, first.frequency):
                raise ValueError("every curve in a family must share one frequency axis")
            if curve.quantity != first.quantity:
                raise ValueError(
                    f"cannot mix {first.quantity!r} and {curve.quantity!r} in one family"
                )
        if self.reference is not None and not 0 <= self.reference < len(self.curves):
            raise ValueError(
                f"reference index {self.reference} is outside 0..{len(self.curves) - 1}"
            )

    def __len__(self) -> int:
        return len(self.curves)

    @property
    def frequency(self) -> np.ndarray:
        return self.curves[0].frequency

    @property
    def quantity(self) -> str:
        return self.curves[0].quantity

    @property
    def valid_below(self) -> float | None:
        """The most restrictive limit in the family.

        A comparison is only as trustworthy as its least trustworthy member, and the point
        of a family is that its members get read against each other.
        """
        limits = [c.valid_below for c in self.curves if c.valid_below is not None]
        return min(limits) if limits else None

    def baseline(self) -> ResponseCurve | None:
        return None if self.reference is None else self.curves[self.reference]

    def deltas(self) -> list[np.ndarray]:
        """Each curve's level relative to the reference, in dB.

        A ratio of magnitudes rather than a difference of complex values: the question a
        delta view answers is "how much louder or quieter did this make it", not "what is
        the vector difference". The reference curve's own delta is identically zero, which
        is worth drawing as the zero line rather than hiding.
        """
        base = self.baseline()
        if base is None:
            raise ValueError(
                "this family has no reference curve, so there is nothing to measure "
                "deltas against. Set one first."
            )
        floor = np.finfo(float).tiny
        reference = np.maximum(base.magnitude, floor)
        return [
            20.0 * np.log10(np.maximum(c.magnitude, floor) / reference) for c in self.curves
        ]

    def spread(self) -> np.ndarray:
        """Peak-to-peak variation across the family at each frequency, in dB.

        The single most useful scalar to come out of a sweep: it says *where* in the
        spectrum the parameter has any authority at all. A vent area that moves the
        response by 8 dB at 80 Hz and 0.1 dB at 1 kHz is a bass tuning control, and the
        spread curve says so without anyone having to read five overlaid lines.
        """
        floor = np.finfo(float).tiny
        levels = np.array([20.0 * np.log10(np.maximum(c.magnitude, floor)) for c in self.curves])
        return levels.max(axis=0) - levels.min(axis=0)

    def most_sensitive_frequency(self) -> float:
        """Where the parameter has the most influence, within the trusted range."""
        spread = self.spread()
        limit = self.valid_below
        mask = self.frequency <= limit if limit is not None else np.ones_like(spread, dtype=bool)
        if not mask.any():
            mask = np.ones_like(spread, dtype=bool)
        candidates = np.where(mask, spread, -np.inf)
        return float(self.frequency[int(np.argmax(candidates))])

    def summarise(self) -> str:
        """A short verdict on what the parameter actually does."""
        spread = self.spread()
        limit = self.valid_below
        mask = self.frequency <= limit if limit is not None else np.ones_like(spread, dtype=bool)
        trusted = spread[mask] if mask.any() else spread
        where = self.most_sensitive_frequency()

        lines = [f"{self.parameter}: {len(self)} runs, {', '.join(self.labels)}"]
        lines.append(
            f"  authority {trusted.max():.1f} dB peak at {where:.0f} Hz, "
            f"{trusted.mean():.1f} dB average across the trusted range"
        )
        if trusted.max() < 0.5:
            lines.append(
                "  this parameter barely moves the result -- widen the range of values, or "
                "look for the effect somewhere the lumped model can represent"
            )
        if limit is not None:
            lines.append(f"  trustworthy below {limit:.0f} Hz")
        return "\n".join(lines)

    def to_csv(self, path: str) -> None:
        """One column per run, so the family opens as a chart in a spreadsheet."""
        first = self.curves[0]
        header = [f"# parameter: {self.parameter}"]
        header += [f"# {k}: {v}" for k, v in sorted(self.metadata.items())]
        if self.valid_below is not None:
            header.append(f"# valid below: {self.valid_below:.1f} Hz")

        if first.quantity == "pressure":
            columns = [f"SPL_dB [{label}]" for label in self.labels]
            data = [c.spl for c in self.curves]
        else:
            columns = [f"magnitude_{first.unit} [{label}]" for label in self.labels]
            data = [c.magnitude for c in self.curves]
        header.append(",".join(["frequency_Hz", *columns]))

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(header) + "\n")
            for row in zip(self.frequency, *data):
                handle.write(",".join(f"{v:.6g}" for v in row) + "\n")

    def with_reference(self, index: int | None) -> "CurveFamily":
        return replace(self, reference=index)


def format_value(value: Any) -> str:
    """A short label for one swept value, for legends and file names."""
    text = str(value).strip()
    return text if text else "?"


def build_family(
    parameter: str,
    pairs: Sequence[tuple[str, ResponseCurve]],
    reference: int | None = 0,
    metadata: dict[str, Any] | None = None,
) -> CurveFamily:
    """Assemble a family from ``(label, curve)`` pairs, defaulting to the first as base."""
    labels = [format_value(label) for label, _ in pairs]
    curves = [curve for _, curve in pairs]
    if reference is not None and not 0 <= reference < len(curves):
        reference = 0 if curves else None
    return CurveFamily(parameter, labels, curves, reference, metadata or {})
