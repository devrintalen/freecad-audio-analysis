"""Preflight checks — how the workbench keeps a user out of trouble.

Acoustic simulation fails quietly. A model with a driver whose back port goes nowhere,
or a sweep run an octave past the point where the lumped assumption holds, does not
crash: it produces a smooth, confident, wrong curve. Someone new to the field has no way
to tell that curve from a right one.

So every solve is preceded by a validation pass that produces :class:`Diagnostic` objects,
and each diagnostic answers three questions rather than one:

* **what** is wrong,
* **why it matters physically** — the part that teaches,
* **what to do about it**.

Severity decides what happens next. ``ERROR`` blocks the solve. ``WARNING`` lets it run
but annotates the results. ``INFO`` records an assumption worth seeing.

Checks are registered functions taking an analysis object and yielding diagnostics. They
are pure and headless-testable; nothing here imports FreeCADGui. Tiers 1 and beyond
register their own checks against this same framework — see the catalogue in
STRUCTURE.md §6.7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Iterable, Iterator

from freecad.audio_analysis.physics import air


class Severity(IntEnum):
    """Ordered so the worst sorts first."""

    ERROR = 3
    WARNING = 2
    INFO = 1

    @property
    def label(self) -> str:
        return {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "info"}[self]


@dataclass(frozen=True)
class Diagnostic:
    """One finding about an analysis setup."""

    severity: Severity
    #: Stable identifier, so results can reference a diagnostic and tests can assert on it.
    code: str
    #: What is wrong, in one line.
    message: str
    #: Why it matters physically. This is the part that teaches; never leave it empty.
    why: str = ""
    #: The concrete action to take.
    remedy: str = ""
    #: Section of STRUCTURE.md that explains the underlying physics.
    reference: str = ""
    #: Label of the object concerned, if any.
    subject: str = ""

    def format(self) -> str:
        """Multi-line rendering for the report view or a console."""
        head = f"[{self.severity.label}] {self.message}"
        if self.subject:
            head = f"[{self.severity.label}] {self.subject}: {self.message}"
        lines = [head]
        if self.why:
            lines.append(f"    why:    {self.why}")
        if self.remedy:
            lines.append(f"    action: {self.remedy}")
        if self.reference:
            lines.append(f"    see:    {self.reference}")
        return "\n".join(lines)


@dataclass
class CheckReport:
    """The outcome of a validation pass."""

    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]

    @property
    def can_solve(self) -> bool:
        """True if nothing blocks a solve. Warnings do not block."""
        return not self.errors

    def sorted(self) -> list[Diagnostic]:
        """Worst first, then by code for stable output."""
        return sorted(self.diagnostics, key=lambda d: (-d.severity, d.code))

    def format(self) -> str:
        if not self.diagnostics:
            return "No issues found."
        return "\n".join(d.format() for d in self.sorted())

    def summary(self) -> str:
        errors, warnings = len(self.errors), len(self.warnings)
        info = len(self.diagnostics) - errors - warnings
        return f"{errors} error(s), {warnings} warning(s), {info} note(s)"


CheckFunction = Callable[[Any], Iterable[Diagnostic]]

_REGISTRY: list[CheckFunction] = []


def check(func: CheckFunction) -> CheckFunction:
    """Register a check. Used as a decorator."""
    _REGISTRY.append(func)
    return func


def registered_checks() -> tuple[CheckFunction, ...]:
    return tuple(_REGISTRY)


def run_checks(analysis: Any) -> CheckReport:
    """Run every registered check against ``analysis``.

    A check that raises is reported as an error rather than propagating: a broken check
    must not stop the user seeing the findings of the others.
    """
    report = CheckReport()
    for func in _REGISTRY:
        try:
            report.diagnostics.extend(func(analysis))
        except Exception as exc:  # noqa: BLE001 -- one bad check must not hide the rest
            report.diagnostics.append(
                Diagnostic(
                    severity=Severity.ERROR,
                    code="check-failed",
                    message=f"Internal check {func.__name__} failed: {exc}",
                    why="This is a workbench defect, not a problem with your model.",
                    remedy="Please report it, and treat the remaining findings as incomplete.",
                )
            )
    return report


# ---------------------------------------------------------------------------------
# Structural checks. Tiers 1+ add checks for network topology, meshing and solvers.
# ---------------------------------------------------------------------------------

# Ranges outside which a value is far likelier to be a typo or a unit error than a real
# operating condition. Deliberately wide: -40 C to +70 C, and pressure from Everest to a
# pressurised cabin.
PLAUSIBLE_TEMPERATURE_K = (233.0, 343.0)
PLAUSIBLE_PRESSURE_PA = (30_000.0, 120_000.0)


def _members(analysis: Any) -> list[Any]:
    return list(getattr(analysis, "Group", []) or [])


def _find(analysis: Any, type_name: str) -> list[Any]:
    from freecad.audio_analysis.objects.base import is_audio_object

    return [o for o in _members(analysis) if is_audio_object(o, type_name)]


@check
def check_has_environment(analysis: Any) -> Iterator[Diagnostic]:
    """An analysis needs exactly one medium definition."""
    environments = _find(analysis, "Audio::Environment")

    if not environments:
        yield Diagnostic(
            severity=Severity.ERROR,
            code="no-environment",
            message="This analysis has no Environment.",
            why=(
                "Every acoustic result depends on the medium. Density and speed of sound "
                "set the wavelength, the radiation impedance and the boundary-layer "
                "thickness, so without them there is nothing to solve against."
            ),
            remedy="Add an Environment object to the analysis.",
            reference="STRUCTURE.md §6.2",
        )
    elif len(environments) > 1:
        yield Diagnostic(
            severity=Severity.ERROR,
            code="multiple-environments",
            message=f"This analysis has {len(environments)} Environments; it needs one.",
            why="Two different media would make the result ambiguous.",
            remedy="Delete all but one, or split the study into separate analyses.",
            subject=analysis.Label,
        )


@check
def check_environment_is_plausible(analysis: Any) -> Iterator[Diagnostic]:
    """Catch unit slips and typos in the medium before they poison every result."""
    for env in _find(analysis, "Audio::Environment"):
        try:
            temperature = env.Temperature.getValueAs("K").Value
            pressure = env.StaticPressure.getValueAs("Pa").Value
        except AttributeError:
            continue

        low, high = PLAUSIBLE_TEMPERATURE_K
        if not low <= temperature <= high:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="implausible-temperature",
                message=f"Temperature is {temperature:.1f} K ({air.to_celsius(temperature):.0f} C).",
                why=(
                    "That is outside any normal operating condition. A common cause is "
                    "entering degrees Celsius into a field that expects kelvin."
                ),
                remedy=f"Expected roughly {low:.0f}-{high:.0f} K. Room temperature is 293.15 K.",
                subject=env.Label,
            )

        low, high = PLAUSIBLE_PRESSURE_PA
        if not low <= pressure <= high:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="implausible-pressure",
                message=f"Static pressure is {pressure:.0f} Pa.",
                why=(
                    "That is far from atmospheric. Note that FreeCAD stores pressure "
                    "internally in kilopascals, so a raw value of 101325 would be a "
                    "thousand atmospheres."
                ),
                remedy="Sea-level atmospheric pressure is 101325 Pa.",
                subject=env.Label,
            )


@check
def check_analysis_is_not_empty(analysis: Any) -> Iterator[Diagnostic]:
    """An analysis holding only an Environment cannot produce a result yet."""
    members = _members(analysis)
    substantive = [
        o for o in members
        if not getattr(getattr(o, "Proxy", None), "Type", "").endswith("Environment")
    ]
    if not substantive:
        yield Diagnostic(
            severity=Severity.INFO,
            code="analysis-empty",
            message="This analysis has a medium but nothing to simulate yet.",
            why="A result needs at least a source and somewhere for the sound to go.",
            remedy=(
                "Add a Driver and the volumes it radiates into. Starting from a template "
                "for your device type will wire the topology correctly."
            ),
            reference="STRUCTURE.md §6.7",
            subject=analysis.Label,
        )


def report_lumped_validity(
    largest_dimension_m: float,
    max_frequency: float,
    properties: air.AirProperties | None = None,
) -> Diagnostic:
    """Compare a planned sweep against the lumped-element validity limit.

    Not a registered check -- it needs a frequency range and a dimension, which arrive
    with the Tier 1 objects. Exposed now because the results layer must annotate every
    lumped curve with this, and the logic belongs in one place.
    """
    properties = properties or air.AirProperties.at()
    limit = properties.lumped_validity_limit(largest_dimension_m)
    size_mm = largest_dimension_m * 1000.0

    if max_frequency <= limit:
        return Diagnostic(
            severity=Severity.INFO,
            code="lumped-validity-ok",
            message=(
                f"Lumped modelling is valid across the requested range "
                f"(limit {limit:.0f} Hz for a {size_mm:.0f} mm cavity)."
            ),
            reference="STRUCTURE.md §2.4",
        )

    return Diagnostic(
        severity=Severity.WARNING,
        code="beyond-lumped-validity",
        message=(
            f"Sweep reaches {max_frequency:.0f} Hz but lumped modelling of a "
            f"{size_mm:.0f} mm cavity is only valid to about {limit:.0f} Hz."
        ),
        why=(
            "A cavity behaves as a single compliance only while it is small against the "
            "wavelength. Above that it develops internal standing waves and path-length "
            "differences that a lumped model cannot represent, so the curve stays smooth "
            "and confident while becoming progressively wrong."
        ),
        remedy=(
            f"Trust the result below ~{limit:.0f} Hz. For the range above it, use a 3D "
            f"solve (Tier 2 or 3). Results beyond the limit are marked on the plot."
        ),
        reference="STRUCTURE.md §2.4",
    )
