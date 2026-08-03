"""Where a lumped model stops being true, element by element.

The workbench already refused to plot a confident curve past its validity limit
(CLAUDE.md). This module answers the next question, which is the one a designer actually
has: *which part of my model is the problem, and how much is the rest of it costing me?*

**One number hides too much.** A whole analysis inherits the limit of its worst element,
and for an over-ear headphone that is always the cup — 407 Hz across 105 mm. Quoted alone
it reads as though the entire model expires there. It does not: in the same analysis the
pad-seal leak is a valid lumped element to 10 kHz, the rear vent to 1.3 kHz, the tweeter's
sealed chamber to 2.2 kHz. Knowing that the cup is the binding constraint tells you that
a 3D solve of *the cup* would lift the whole result, and that the leak model — the single
biggest influence on measured bass — was never the weak link.

**The limit is a slope, not a cliff.** A cavity's lumped impedance is in error by

    ``kL cot(kL)``  relative to the exact closed-tube result,

which is 0.45 dB at a sixteenth of a wavelength, 2.1 dB at an eighth, and 5.9 dB not far
above. So two thresholds are reported rather than one: ``confident_below`` at λ/16, where
the error is under half a decibel, and ``limit`` at λ/8, past which the model is being
used outside its assumptions. Between them the answer is worth reading and worth
distrusting, which is exactly the sort of thing a single boolean cannot express.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from freecad.audio_analysis.physics import air

#: Fraction of a wavelength at which a lumped element is considered to have expired.
#: An eighth is the usual engineering criterion and costs about 2 dB.
LIMIT_FRACTION = 0.125

#: The tighter threshold, below which the error is under half a decibel.
CONFIDENT_FRACTION = 0.0625


@dataclass(frozen=True)
class ElementLimit:
    """One element's own ceiling."""

    name: str
    kind: str
    #: Metres, or None when this element imposes no lumped limit.
    length: float | None
    #: How the length was arrived at — measured, assumed, or derived.
    basis: str
    #: Hertz, or None.
    limit: float | None

    @property
    def confident_below(self) -> float | None:
        return None if self.limit is None else self.limit * CONFIDENT_FRACTION / LIMIT_FRACTION

    def format(self, labels: dict[str, str] | None = None) -> str:
        name = (labels or {}).get(self.name, self.name)
        if self.limit is None:
            return f"  {name:<18} {'exact':>9}   {self.kind} -- not a lumped approximation"
        return (
            f"  {name:<18} {self.limit:>7.0f} Hz   {self.kind}, "
            f"{self.length * 1000:.0f} mm ({self.basis})"
        )


@dataclass(frozen=True)
class ValidityReport:
    """Every element's ceiling, and which one binds."""

    limits: list[ElementLimit]

    @property
    def bounded(self) -> list[ElementLimit]:
        """Only the elements that impose a limit, worst first."""
        return sorted(
            (item for item in self.limits if item.limit is not None),
            key=lambda item: item.limit,
        )

    @property
    def binding(self) -> ElementLimit | None:
        """The element that sets the model's ceiling."""
        bounded = self.bounded
        return bounded[0] if bounded else None

    @property
    def limit(self) -> float | None:
        """The model's ceiling in hertz, or None if nothing imposes one."""
        binding = self.binding
        return None if binding is None else binding.limit

    @property
    def confident_below(self) -> float | None:
        """Below this, every element is inside half a decibel."""
        limit = self.limit
        return None if limit is None else limit * CONFIDENT_FRACTION / LIMIT_FRACTION

    @property
    def headroom(self) -> float | None:
        """How much the ceiling would rise if the binding element were solved in 3D.

        The ratio to the *next* constraint. A large number means one part of the model is
        holding back all the rest, and says where a 3D solve would buy the most.
        """
        bounded = self.bounded
        if len(bounded) < 2 or not bounded[0].limit:
            return None
        return bounded[1].limit / bounded[0].limit

    def uses_assumed_dimensions(self) -> list[ElementLimit]:
        """Elements whose limit rests on a guess about shape rather than a measurement."""
        return [item for item in self.bounded if "optimistic" in item.basis]

    def format(self, labels: dict[str, str] | None = None) -> str:
        """The table, optionally under the user's own object labels."""
        if not self.limits:
            return "Nothing in this model imposes a lumped validity limit."
        names = labels or {}
        lines = []
        binding = self.binding
        if binding is None:
            lines.append("No element imposes a lumped validity limit.")
        else:
            lines.append(
                f"Lumped validity: under 0.5 dB below {self.confident_below:.0f} Hz, "
                f"about 2 dB by {self.limit:.0f} Hz, set by "
                f"{names.get(binding.name, binding.name)}."
            )
            headroom = self.headroom
            if headroom and headroom > 1.5:
                lines.append(
                    f"  {names.get(binding.name, binding.name)} alone holds the model back "
                    f"by a factor of {headroom:.1f}; everything else is good to "
                    f"{self.bounded[1].limit:.0f} Hz or more."
                )
        lines.append("  element            ceiling   why")
        lines.extend(item.format(names) for item in self.bounded)
        lines.extend(item.format(names) for item in self.limits if item.limit is None)
        return "\n".join(lines)


def assess(elements: Sequence[Any], medium: air.AirProperties | None = None) -> ValidityReport:
    """Build a :class:`ValidityReport` for a sequence of network elements."""
    medium = medium or air.AirProperties.at()
    limits: list[ElementLimit] = []
    for element in elements:
        length, basis = element.characteristic_length()
        limit = (
            medium.lumped_validity_limit(length, LIMIT_FRACTION)
            if length and length > 0.0
            else None
        )
        limits.append(
            ElementLimit(
                name=element.name,
                kind=type(element).__name__,
                length=length,
                basis=basis,
                limit=limit,
            )
        )
    return ValidityReport(limits)
