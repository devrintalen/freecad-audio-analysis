"""Sweeping one parameter across runs.

The feature most likely to change how someone designs (STRUCTURE.md §6.9). A lumped solve
takes milliseconds, so trying five vent areas costs less than typing them, and the answer
to "what do my rear openings actually do" is a family of curves rather than an argument.

**The sweep never leaves the model changed.** It writes a value, solves, and puts the
original back — including when a run raises. A design tool that quietly leaves the last
swept value in place would corrupt the model it was meant to explore, and the corruption
would be invisible until the next solve gave an answer nobody asked for.

**Values are text.** ``["4 cm^2", "8 cm^2", "16 cm^2"]`` rather than bare numbers, because
a swept property may be a length, an area, a plain float or an enumeration, and units that
travel with the value cannot be misread. They are coerced to whatever the target property
already holds, so a unit slip is caught when the sweep runs rather than becoming a
plausible wrong curve.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider
from freecad.audio_analysis.objects.network_objects import quantity


class SweepError(ValueError):
    """Raised when a sweep cannot be run as described."""


def coerce(current: Any, text: str) -> Any:
    """Turn one swept value into whatever the target property already holds.

    Keyed off the current value's type rather than the property's declared type, which is
    not exposed uniformly across FreeCAD property classes. Quantities take the text
    verbatim so ``"8 cm^2"`` and ``"800 mm^2"`` both work; a bare number in a quantity
    field is interpreted in FreeCAD's internal unit, which is exactly the sort of silent
    error the units convention in CLAUDE.md exists to prevent, so it is refused.
    """
    text = text.strip()
    if not text:
        raise SweepError("a swept value cannot be blank")

    if isinstance(current, FreeCAD.Units.Quantity):
        try:
            value = FreeCAD.Units.Quantity(text)
        except (ValueError, TypeError) as exc:
            raise SweepError(f"{text!r} is not a valid quantity") from exc
        if not value.Unit == current.Unit:
            raise SweepError(
                f"{text!r} has units of {value.Unit or 'none'} but the property expects "
                f"{current.Unit}. Write the unit out, e.g. '8 cm^2' rather than '8'."
            )
        return value

    if isinstance(current, bool):
        lowered = text.lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise SweepError(f"{text!r} is not a true/false value")

    if isinstance(current, int):
        try:
            return int(text)
        except ValueError as exc:
            raise SweepError(f"{text!r} is not a whole number") from exc

    if isinstance(current, float):
        try:
            return float(text)
        except ValueError as exc:
            raise SweepError(f"{text!r} is not a number") from exc

    return text  # Strings and enumerations pass through.


class ParameterSweep(AudioObject):
    """Vary one property of one object and collect the responses."""

    Type = "Audio::ParameterSweep"

    def __init__(self, obj: Any) -> None:
        super().__init__(obj)
        self.family = None

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyLink", "Target", "Sweep",
                "Object whose property is varied.",
            ),
            PropertySpec(
                "App::PropertyString", "Property", "Sweep",
                "Name of the property to vary, exactly as it appears in the property "
                "editor -- for example 'Area' on a Port, or 'SpecificResistance' on a "
                "damping mesh.",
                default="",
            ),
            PropertySpec(
                "App::PropertyStringList", "Values", "Sweep",
                "Values to try, written with their units: '4 cm^2', '8 cm^2'. A bare "
                "number in a field that expects a unit is refused rather than guessed at.",
                default=[],
            ),
            PropertySpec(
                "App::PropertyLink", "Observe", "Sweep",
                "Node whose pressure is compared across runs. Leave empty to use the "
                "first volume in the analysis, which for a headphone is the ear cavity.",
            ),
            PropertySpec(
                "App::PropertyInteger", "Reference", "Sweep",
                "Index of the run that deltas are measured against, counting from zero. "
                "Negative for no baseline.",
                default=0,
            ),
            PropertySpec(
                "App::PropertyString", "Status", "Results",
                "Outcome of the last sweep", default="not run", read_only=True,
            ),
        )

    def loads(self, state: Any) -> None:
        super().loads(state)
        self.family = None  # Results are transient, as for the solver.

    # -- running -----------------------------------------------------------------------

    def _observed_node(self, obj: Any, analysis: Any) -> str:
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.network_objects import AcousticVolume

        if obj.Observe is not None:
            return obj.Observe.Name
        volumes = [
            o for o in getattr(analysis, "Group", []) or []
            if is_audio_object(o, AcousticVolume.Type)
        ]
        if not volumes:
            raise SweepError(
                "this analysis has no volume to observe. Set Observe to the node whose "
                "pressure you want compared."
            )
        return volumes[0].Name

    def validate(self, obj: Any) -> None:
        """Raise :class:`SweepError` if this sweep is not runnable as described."""
        if obj.Target is None:
            raise SweepError("no target object: nothing to vary.")
        if not obj.Property:
            raise SweepError("no property named.")
        if not hasattr(obj.Target, obj.Property):
            raise SweepError(
                f"{obj.Target.Label} has no property {obj.Property!r}. Names are "
                f"case-sensitive and must match the property editor exactly."
            )
        if len(obj.Values) < 2:
            raise SweepError(
                "a sweep needs at least two values; with one there is nothing to compare."
            )

    def run(self, obj: Any, analysis: Any) -> Any:
        """Solve once per value and return the resulting :class:`CurveFamily`.

        The target's original value is restored whatever happens, so exploring a design
        never alters it.
        """
        from freecad.audio_analysis.builder import build_network, sweep_frequencies
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.study import LumpedSolver
        from freecad.audio_analysis.results.family import build_family

        self.validate(obj)
        target, name = obj.Target, obj.Property
        node = self._observed_node(obj, analysis)
        frequencies = sweep_frequencies(analysis)

        solvers = [o for o in analysis.Group if is_audio_object(o, LumpedSolver.Type)]
        solver = solvers[0] if solvers else None

        original = getattr(target, name)
        pairs = []
        try:
            for text in obj.Values:
                setattr(target, name, coerce(original, text))
                target.Document.recompute()
                network, medium = build_network(analysis)
                limit = (
                    solver.Proxy.validity_limit(solver, medium) if solver is not None else None
                )
                solution = network.solve(frequencies, valid_below=limit)
                pairs.append((text, solution.pressure(node)))
        finally:
            setattr(target, name, original)
            target.Document.recompute()

        reference = obj.Reference if obj.Reference >= 0 else None
        family = build_family(
            f"{target.Label}.{name}",
            pairs,
            reference=reference,
            metadata={"observed": node, "solver": "lumped network"},
        )
        self.family = family
        obj.Status = (
            f"{len(pairs)} runs, {family.spread().max():.1f} dB peak spread at "
            f"{family.most_sensitive_frequency():.0f} Hz"
        )
        return family


def make_parameter_sweep(doc: Any, analysis: Any = None, name: str = "ParameterSweep") -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    ParameterSweep(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj
