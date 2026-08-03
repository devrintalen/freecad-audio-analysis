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
STRUCTURE.md §6.8.
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
            reference="STRUCTURE.md §6.8",
            subject=analysis.Label,
        )


@check
def check_network_topology(analysis: Any) -> Iterator[Diagnostic]:
    """Tier 1: the network must be buildable and every node must have a path to ground."""
    from freecad.audio_analysis.objects.network_objects import Driver

    drivers = _find(analysis, Driver.Type)
    if not drivers and not _members(analysis):
        return  # Empty analysis; check_analysis_is_not_empty already said so.
    if not drivers:
        return  # Nothing to build yet; not an error until a solve is attempted.

    from freecad.audio_analysis.builder import BuildError, build_network

    try:
        network, _ = build_network(analysis)
    except BuildError as exc:
        yield Diagnostic(
            severity=Severity.ERROR,
            code="network-unbuildable",
            message=str(exc),
            why="The network could not be assembled, so nothing can be solved.",
            remedy="Correct the object named in the message.",
            reference="STRUCTURE.md §6.6",
        )
        return

    for node in network.floating_nodes():
        from freecad.audio_analysis.builder import label_for_node

        yield Diagnostic(
            severity=Severity.ERROR,
            code="floating-node",
            message=f"Node '{label_for_node(analysis, node)}' has only one connection.",
            why=(
                "A node with a single connection has no path back to ambient, so no "
                "volume velocity can flow and the system has no solution. It usually "
                "means a port or mesh was left dangling."
            ),
            remedy="Connect a second element to it, or delete the element that leads to it.",
            reference="STRUCTURE.md §6.6",
        )


@check
def check_driver_connections(analysis: Any) -> Iterator[Diagnostic]:
    """An unset node means the exterior, which is meaningful but easy to mean by accident."""
    from freecad.audio_analysis.objects.network_objects import Driver

    for driver in _find(analysis, Driver.Type):
        if driver.FrontNode is None and driver.BackNode is None:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="driver-unloaded",
                message="Both sides of this driver connect to the exterior.",
                why=(
                    "Front and back radiation then cancel almost completely at low "
                    "frequency, as they do for a bare driver with no baffle. That is a "
                    "real configuration, but rarely the one intended."
                ),
                remedy=(
                    "Connect the front to the ear or listening cavity, and the back to "
                    "the enclosure volume."
                ),
                subject=driver.Label,
                reference="STRUCTURE.md §6.6",
            )
        elif driver.BackNode is None:
            yield Diagnostic(
                severity=Severity.INFO,
                code="driver-open-back",
                message="This driver's back radiates directly to the exterior.",
                why=(
                    "Modelled as a fully open back with no rear cavity at all -- no "
                    "back-volume stiffness and no vent impedance."
                ),
                remedy=(
                    "Intended for a fully open design. For a vented cup, connect the back "
                    "to a volume and add a Port for the opening."
                ),
                subject=driver.Label,
            )


@check
def check_crossover_assignments(analysis: Any) -> Iterator[Diagnostic]:
    """Every crossover branch must feed drivers, and no driver may be fed by two."""
    from freecad.audio_analysis.objects.crossover import CrossoverFilter

    branches = _find(analysis, CrossoverFilter.Type)
    owners: dict[str, list[str]] = {}
    for branch in branches:
        drivers = [d for d in branch.Drivers if d is not None]
        if not drivers:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="crossover-unattached",
                message="This crossover feeds no drivers, so it does nothing.",
                why=(
                    "A filter only exists in the model through the drivers it is wired to. "
                    "An unattached one is silently ignored, which looks identical to a "
                    "crossover that is not working."
                ),
                remedy="Add the driver it feeds to the Drivers list.",
                subject=branch.Label,
                reference="STRUCTURE.md §6.6",
            )
        for driver in drivers:
            owners.setdefault(driver.Name, []).append(branch.Label)

    for name, labels in owners.items():
        if len(labels) > 1:
            yield Diagnostic(
                severity=Severity.ERROR,
                code="crossover-conflict",
                message=f"This driver is fed by {len(labels)} crossovers: {', '.join(labels)}.",
                why=(
                    "A driver has one pair of terminals, so only one branch can drive it. "
                    "Which one applied would depend on object order, which is not a "
                    "property anyone should be relying on."
                ),
                remedy=(
                    "Remove it from all but one. To cascade two filter shapes, raise the "
                    "order of a single branch instead."
                ),
                subject=name,
            )


@check
def check_crossover_realisation(analysis: Any) -> Iterator[Diagnostic]:
    """Catch filters that cannot be built, and passive ones asked for the impossible."""
    from freecad.audio_analysis.objects.crossover import CrossoverFilter
    from freecad.audio_analysis.physics.crossover import CrossoverError

    for branch in _find(analysis, CrossoverFilter.Type):
        try:
            branch.Proxy.filter(branch)
        except CrossoverError as exc:
            yield Diagnostic(
                severity=Severity.ERROR,
                code="crossover-unrealisable",
                message=str(exc),
                why="This combination of alignment and order does not describe a filter.",
                remedy="Change the alignment or the order.",
                subject=branch.Label,
            )
            continue

        if not CrossoverFilter.is_passive(branch):
            continue

        if branch.Gain > 0.0:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="passive-cannot-amplify",
                message=f"A passive branch cannot apply {branch.Gain:+.1f} dB of gain.",
                why=(
                    "Inductors, capacitors and resistors only ever remove energy. The gain "
                    "is being ignored, so this branch will come out louder in the model "
                    "than a built one would be."
                ),
                remedy=(
                    "Pad the *other* driver down by the same amount instead, or switch this "
                    "branch to Active."
                ),
                subject=branch.Label,
            )

        if branch.Delay.getValueAs("s").Value:
            yield Diagnostic(
                severity=Severity.WARNING,
                code="passive-cannot-delay",
                message="A passive branch cannot apply a delay.",
                why=(
                    "Pure delay has no passive realisation, so the value is ignored. In a "
                    "real build the same alignment is achieved by physically offsetting the "
                    "driver, which is a geometry change rather than a circuit one."
                ),
                remedy="Switch to Active, or move the driver in the CAD model.",
                subject=branch.Label,
            )

        for driver in (d for d in branch.Drivers if d is not None):
            if not 0.5 <= branch.NominalImpedance / max(driver.Re, 1e-9) <= 2.0:
                yield Diagnostic(
                    severity=Severity.WARNING,
                    code="crossover-impedance-mismatch",
                    message=(
                        f"Component values assume {branch.NominalImpedance:.0f} ohm but "
                        f"{driver.Label} has Re = {driver.Re:.0f} ohm."
                    ),
                    why=(
                        "Passive component values are computed against a nominal load. Get "
                        "it badly wrong and the corner frequency and slope both move, "
                        "usually by more than any amount of fine tuning will recover."
                    ),
                    remedy=f"Set NominalImpedance near {driver.Re:.0f} ohm.",
                    subject=branch.Label,
                )


@check
def check_crossover_polarity(analysis: Any) -> Iterator[Diagnostic]:
    """A complementary Linkwitz-Riley pair may need one driver wired backwards.

    The single least intuitive fact about crossovers, and invisible in a parts list: an
    Nth-order filter rotates phase by N quarter-turns, so at the crossover frequency the
    two branches are N*90 degrees apart. At LR4 that is a full turn and the drivers sum in
    phase. At LR2 it is half a turn and they cancel -- a deep notch exactly where both
    drivers are working hardest, which sounds like a missing midrange rather than like a
    wiring error.

    **The rule assumes the drivers themselves are flat and in phase across the crossover
    region, and real ones are not.** A woofer well above its resonance and a tweeter only
    just above its own each contribute phase of their own, and the two rotations need not
    cancel; ``examples/two_way_study.py`` shows a plausible pair where the right answer is
    the opposite of the rule. So this is raised as a warning rather than an error: it is
    the correct default and an unconsidered polarity is nearly always a mistake, but the
    solve is what settles it, and the remedy says so.
    """
    from freecad.audio_analysis.objects.crossover import CrossoverFilter

    branches = [
        b for b in _find(analysis, CrossoverFilter.Type)
        if str(b.Response) in ("Lowpass", "Highpass")
    ]
    lows = [b for b in branches if str(b.Response) == "Lowpass"]
    highs = [b for b in branches if str(b.Response) == "Highpass"]
    if len(lows) != 1 or len(highs) != 1:
        return  # Not a simple two-way; the phase bookkeeping is the user's to do.

    low, high = lows[0], highs[0]
    if str(low.Alignment) != str(high.Alignment) or low.Order != high.Order:
        return
    if str(low.Alignment) != "Linkwitz-Riley":
        return
    if abs(low.Frequency.getValueAs("Hz").Value - high.Frequency.getValueAs("Hz").Value) > 1.0:
        return

    # Order n rotates the pair by n quarter-turns; a half-turn needs one branch reversed.
    inversion_needed = (low.Order // 2) % 2 == 1
    drivers = [d for d in list(low.Drivers) + list(high.Drivers) if d is not None]
    if not drivers:
        return
    inverted = sum(1 for d in drivers if d.Inverted)
    branch_inverted = inverted % 2 == 1

    if branch_inverted == inversion_needed:
        return

    settle = (
        " Then solve it both ways and compare at the crossover frequency: the rule assumes "
        "both drivers are flat and in phase there, and real ones are not, so the solve is "
        "what settles it."
    )
    if inversion_needed:
        message = f"An LR{low.Order} pair normally needs one driver inverted, and none is."
        remedy = f"Try ticking Inverted on one of {', '.join(d.Label for d in drivers)}." + settle
        why = (
            "An Nth-order filter rotates the pair by N quarter-turns, so at this order the "
            "two branches arrive 180 degrees apart and cancel instead of summing. Expect a "
            "notch right where both drivers are contributing most -- deep if their levels "
            "match there, a dull midrange if they do not."
        )
    else:
        message = f"An LR{low.Order} pair normally sums in phase, but a driver is inverted."
        remedy = "Try unticking Inverted, or confirm the inversion is deliberate." + settle
        why = (
            "At this order the branches already arrive in phase, so reversing one turns a "
            "flat sum into a cancellation."
        )

    yield Diagnostic(
        severity=Severity.WARNING,
        code="crossover-polarity",
        message=message,
        why=why,
        remedy=remedy,
        reference="STRUCTURE.md §2.4",
        subject=f"{low.Label} / {high.Label}",
    )


@check
def check_crossover_within_validity(analysis: Any) -> Iterator[Diagnostic]:
    """A crossover placed above the lumped limit cannot be designed with a lumped model.

    Worth saying plainly. Crossover frequencies live in the low kilohertz; a lumped model
    of an over-ear cup runs out at a few hundred hertz. Everything the solver reports about
    the crossover region can then be smooth, plausible and untrustworthy.
    """
    from freecad.audio_analysis.objects.crossover import CrossoverFilter
    from freecad.audio_analysis.objects.study import LumpedSolver

    branches = [
        b for b in _find(analysis, CrossoverFilter.Type)
        if str(b.Response) in ("Lowpass", "Highpass")
    ]
    solvers = _find(analysis, LumpedSolver.Type)
    if not branches or not solvers:
        return

    dimension = solvers[0].LargestDimension.getValueAs("m").Value
    if dimension <= 0.0:
        return  # check_sweep_against_validity already asks for this.

    from freecad.audio_analysis.builder import medium_of

    limit = medium_of(analysis).lumped_validity_limit(dimension)
    highest = max(b.Frequency.getValueAs("Hz").Value for b in branches)
    if highest <= limit:
        return

    yield Diagnostic(
        severity=Severity.WARNING,
        code="crossover-beyond-validity",
        message=(
            f"The crossover is at {highest:.0f} Hz but this model is only lumped-valid to "
            f"about {limit:.0f} Hz."
        ),
        why=(
            "The crossover region is where the two drivers interact most and where the "
            "summed response is most sensitive to phase -- and it sits entirely in the "
            "range where a lumped model no longer represents the cavity. The curve there "
            "is not evidence about this design."
        ),
        remedy=(
            "Use Tier 1 to set levels and low-frequency behaviour, and a 3D solve for the "
            "crossover region itself. Comparing two candidate crossovers against each "
            "other stays useful; treating the absolute curve as a prediction does not."
        ),
        reference="STRUCTURE.md §2.4",
    )


def analysis_validity(analysis: Any):
    """``(report, labels)`` for an analysis, or ``(None, {})`` if it cannot be built."""
    from freecad.audio_analysis.builder import (
        BuildError,
        build_network,
        element_labels,
        medium_of,
    )
    from freecad.audio_analysis.objects.network_objects import Driver

    if not _find(analysis, Driver.Type):
        return None, {}
    try:
        network, medium = build_network(analysis)
    except BuildError:
        return None, {}
    return network.validity(medium), element_labels(analysis)


@check
def check_element_validity(analysis: Any) -> Iterator[Diagnostic]:
    """Attribute the model's validity limit to the element that sets it.

    One number hides too much. An over-ear analysis always expires at the cup, and quoted
    alone that reads as though the whole model dies at 400 Hz. It does not: in the same
    analysis the pad seal is a valid lumped element to 10 kHz and the rear vent to 1.3.
    Knowing which part binds is what turns "this is invalid" into a decision about where
    a 3D solve would actually buy something.
    """
    report, labels = analysis_validity(analysis)
    if report is None or report.binding is None:
        return

    binding = report.binding
    name = labels.get(binding.name, binding.name)
    headroom = report.headroom
    detail = report.format(labels)

    yield Diagnostic(
        severity=Severity.INFO,
        code="validity-per-element",
        message=(
            f"Under 0.5 dB below {report.confident_below:.0f} Hz, about 2 dB by "
            f"{report.limit:.0f} Hz, set by {name}."
        ),
        why=(
            "A lumped element holds while the thing it stands for is small against a "
            "wavelength, and each part of the model reaches that point somewhere "
            "different. The error grows smoothly rather than stopping suddenly: it is "
            "half a decibel at a sixteenth of a wavelength and about two at an eighth."
            + (
                f" Here {name} alone holds the model back by a factor of {headroom:.1f}."
                if headroom and headroom > 1.5
                else ""
            )
        ),
        remedy=f"Per element:\n{detail}",
        reference="STRUCTURE.md §2.4",
    )


@check
def check_cavity_dimensions_are_measured(analysis: Any) -> Iterator[Diagnostic]:
    """A volume with no measured span has its limit guessed, and guessed generously."""
    report, labels = analysis_validity(analysis)
    if report is None:
        return

    assumed = report.uses_assumed_dimensions()
    if not assumed:
        return
    names = ", ".join(labels.get(item.name, item.name) for item in assumed)

    yield Diagnostic(
        severity=Severity.WARNING,
        code="cavity-shape-assumed",
        message=f"The validity limit for {names} is guessed from volume alone.",
        why=(
            "A volume does not fix a shape, and shape is what decides where standing "
            "waves start. The guess assumes the most compact shape possible -- a sphere "
            "-- so it is the most optimistic answer available. A 200 cm3 headphone cup "
            "looks valid to 593 Hz as a sphere and is actually valid to 407 Hz across "
            "its real 105 mm width, a 46% overstatement in the direction that flatters "
            "the model."
        ),
        remedy=(
            "Extract the cavity from the CAD and link it, which sets the span from the "
            "solid, or type LargestDimension by hand."
        ),
        reference="STRUCTURE.md §6.5",
    )


@check
def check_sweep_against_validity(analysis: Any) -> Iterator[Diagnostic]:
    """Warn when a sweep runs past the frequency where lumped modelling holds."""
    from freecad.audio_analysis.objects.study import FrequencySweep, LumpedSolver

    sweeps = _find(analysis, FrequencySweep.Type)
    solvers = _find(analysis, LumpedSolver.Type)
    if not sweeps or not solvers:
        return

    from freecad.audio_analysis.builder import medium_of

    medium = medium_of(analysis)
    override = solvers[0].LargestDimension.getValueAs("m").Value
    if override > 0.0:
        yield report_lumped_validity(
            override, sweeps[0].Stop.getValueAs("Hz").Value, medium
        )
        return

    report, labels = analysis_validity(analysis)
    if report is None or report.limit is None:
        return
    stop = sweeps[0].Stop.getValueAs("Hz").Value
    if stop <= report.limit:
        return

    binding = report.binding
    yield Diagnostic(
        severity=Severity.WARNING,
        code="beyond-lumped-validity",
        message=(
            f"Sweep reaches {stop:.0f} Hz but this model is lumped-valid to about "
            f"{report.limit:.0f} Hz, set by "
            f"{labels.get(binding.name, binding.name)}."
        ),
        why=(
            "A cavity behaves as a single compliance only while it is small against the "
            "wavelength. Above that it develops internal standing waves and path-length "
            "differences that a lumped model cannot represent, so the curve stays smooth "
            "and confident while becoming progressively wrong."
        ),
        remedy=(
            f"Trust the result below ~{report.confident_below:.0f} Hz and read it with "
            f"care up to {report.limit:.0f} Hz. For the range above, use a 3D solve "
            f"(Tier 2 or 3). Plots mark both thresholds."
        ),
        reference="STRUCTURE.md §2.4",
    )


def check_solution(solution: Any, analysis: Any = None) -> CheckReport:
    """Findings that need a solved result rather than only a setup.

    Not part of the registered pass, which runs *before* a solve. Excursion is the obvious
    case: whether a diaphragm exceeds its linear travel is not a property of the model, it
    is a property of the answer, and it depends on the drive level as much as on the design.
    """
    from freecad.audio_analysis.physics.network import Driver as PhysicsDriver

    report = CheckReport()
    for driver in solution.network.drivers:
        if not isinstance(driver, PhysicsDriver):  # pragma: no cover -- defensive
            continue
        xmax = driver.parameters.Xmax
        if xmax <= 0.0:
            continue
        try:
            excursion = solution.excursion(driver.name).trusted()
        except ValueError:
            continue

        peak = float(excursion.magnitude.max())
        where = float(excursion.frequency[int(excursion.magnitude.argmax())])
        fraction = peak / xmax
        if fraction <= 1.0:
            continue

        report.diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                code="excursion-exceeds-xmax",
                message=(
                    f"Peak excursion is {peak * 1000:.2f} mm at {where:.0f} Hz, "
                    f"{fraction * 100:.0f}% of Xmax."
                ),
                why=(
                    "Beyond Xmax the motor's force factor and the suspension's stiffness "
                    "both stop being constant, so the driver distorts. Every result here "
                    "is a small-signal one and will keep reporting a clean response no "
                    "matter how far past the limit it goes -- the model has no way to "
                    "represent the distortion it is predicting."
                ),
                remedy=(
                    f"Lower the drive voltage, or reduce output below {where:.0f} Hz with "
                    f"a high-pass. Nonlinear behaviour is Tier 5."
                ),
                reference="STRUCTURE.md §6.9",
                subject=driver.name,
            )
        )
    return report


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
