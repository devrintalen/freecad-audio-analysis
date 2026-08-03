"""Study and solver objects for Tier 1.

``FrequencySweep`` says which frequencies to solve at; ``LumpedSolver`` runs the network
and holds the results.

Results are **not persisted in the document**. A lumped solve takes milliseconds, so
storing a few hundred complex points per curve in the ``.FCStd`` would bloat the file to
save time nobody needs, and would let a stale curve outlive the model that produced it.
Re-solving on demand is faster than being wrong. Explicit CSV/FRD export is how a result
leaves.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider
from freecad.audio_analysis.objects.network_objects import quantity


class FrequencySweep(AudioObject):
    """The frequencies a study is solved at.

    Logarithmic spacing by default, at constant resolution per octave: the natural axis
    for audio, where a linear sweep would spend most of its points above 10 kHz.
    """

    Type = "Audio::FrequencySweep"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyFrequency", "Start", "Sweep",
                         "Lowest frequency", default=quantity(20.0, "Hz")),
            PropertySpec("App::PropertyFrequency", "Stop", "Sweep",
                         "Highest frequency", default=quantity(20000.0, "Hz")),
            PropertySpec("App::PropertyInteger", "PointsPerOctave", "Sweep",
                         "Resolution. 24 is ample for smooth curves; raise it to resolve "
                         "a sharp resonance.", default=24),
        )

    def frequencies(self, obj: Any):
        from freecad.audio_analysis.results.curve import log_frequencies

        return log_frequencies(
            obj.Start.getValueAs("Hz").Value,
            obj.Stop.getValueAs("Hz").Value,
            obj.PointsPerOctave,
        )


class LumpedSolver(AudioObject):
    """Runs the lumped network and holds the most recent results in memory."""

    Type = "Audio::SolverLumped"

    def __init__(self, obj: Any) -> None:
        super().__init__(obj)
        self.solution = None

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyLength", "LargestDimension", "Validity",
                         "Largest internal dimension of the model. Sets the frequency "
                         "above which lumped results stop being trustworthy; leave at "
                         "zero to skip the check.", default=quantity(0.0, "mm")),
            PropertySpec("App::PropertyString", "Status", "Results",
                         "Outcome of the last solve", default="not run", read_only=True),
        )

    def loads(self, state: Any) -> None:
        super().loads(state)
        self.solution = None  # Results are transient; never restored from file.

    def validity_limit(self, obj: Any, medium: Any) -> float | None:
        """Frequency above which lumped modelling of this model fails (§2.4)."""
        dimension = obj.LargestDimension.getValueAs("m").Value
        if dimension <= 0.0:
            return None
        return medium.lumped_validity_limit(dimension)

    def solve(self, obj: Any, analysis: Any) -> Any:
        """Build the network from ``analysis`` and solve it. Returns the Solution."""
        from freecad.audio_analysis.builder import build_network, sweep_frequencies

        network, medium = build_network(analysis)
        frequencies = sweep_frequencies(analysis)
        solution = network.solve(frequencies, valid_below=self.validity_limit(obj, medium))
        self.solution = solution
        obj.Status = (
            f"solved {len(frequencies)} points, "
            f"{len(network.elements)} elements, {len(network.node_names())} nodes"
        )
        return solution


class TargetCurve(AudioObject):
    """A response the design is aiming at, loaded from a file.

    For headphones this is the whole argument. There is no room, no reflections and no
    reference except an agreed curve, so "is this right" is a question about distance from
    a target rather than about flatness.

    The file is referenced rather than copied into the document, so editing the target
    outside FreeCAD and recomputing picks up the change. Commercial target curves are
    published research but not redistributable (STRUCTURE.md §6.4), which is why none ships
    with the workbench.
    """

    Type = "Audio::TargetCurve"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyFile", "File", "Target",
                "CSV or FRD file: frequency in hertz, then level in decibels. The level's "
                "absolute value does not matter -- a target fixes shape, and the "
                "comparison level-matches before measuring.",
                default="",
            ),
            PropertySpec(
                "App::PropertyLink", "Observe", "Target",
                "Node whose pressure is compared. Leave empty for the first volume, which "
                "for a headphone is the ear cavity.",
            ),
            PropertySpec(
                "App::PropertyFrequency", "BandStart", "Target",
                "Low end of the comparison band", default=quantity(20.0, "Hz"),
            ),
            PropertySpec(
                "App::PropertyFrequency", "BandStop", "Target",
                "High end of the comparison band. Clipped to the lumped validity limit, "
                "because a deviation figure quoted past it is not evidence.",
                default=quantity(20000.0, "Hz"),
            ),
            PropertySpec(
                "App::PropertyString", "Status", "Results",
                "Outcome of the last comparison", default="not compared", read_only=True,
            ),
        )

    def load(self, obj: Any):
        """Read the referenced file. Raises TargetError with a readable message."""
        from freecad.audio_analysis.results.target import TargetError, load_target

        if not obj.File:
            raise TargetError(f"{obj.Label} has no file set.")
        return load_target(obj.File, label=obj.Label)

    def compare(self, obj: Any, solution: Any, analysis: Any = None):
        """Compare a solved response against this target. Returns a Deviation."""
        from freecad.audio_analysis.results.target import TargetError, compare

        node = obj.Observe.Name if obj.Observe is not None else None
        if node is None:
            nodes = solution.network.node_names()
            if not nodes:
                raise TargetError("this network has no node to compare at.")
            node = nodes[0]

        deviation = compare(
            solution.pressure(node),
            self.load(obj),
            band=(
                obj.BandStart.getValueAs("Hz").Value,
                obj.BandStop.getValueAs("Hz").Value,
            ),
        )
        obj.Status = (
            f"{deviation.rms:.2f} dB RMS over "
            f"{deviation.band[0]:.0f}-{deviation.band[1]:.0f} Hz"
        )
        return deviation


def make_target_curve(doc: Any, analysis: Any = None, name: str = "TargetCurve") -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    TargetCurve(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj


def make_frequency_sweep(doc: Any, analysis: Any = None, name: str = "FrequencySweep") -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    FrequencySweep(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj


def make_lumped_solver(doc: Any, analysis: Any = None, name: str = "LumpedSolver") -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    LumpedSolver(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj
