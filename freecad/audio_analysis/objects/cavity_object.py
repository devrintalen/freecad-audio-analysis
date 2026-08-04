"""The AcousticCavity document object: air derived from CAD, parametrically.

A ``Part::FeaturePython``, deliberately, so the extracted air becomes a **real solid you
can see in the 3D view**. Cavity extraction is a step where it is easy to get a plausible
wrong answer -- the wrong region, an unclosed model, a cap that missed -- and looking at
the result is the fastest way to catch that. It also means the cavity is ready to mesh
when Tier 2 arrives, and that ``AcousticVolume`` can simply reference it.

Recomputation is opt-out. Fusing and subtracting a full assembly takes on the order of
fifteen seconds, which is fine on demand and intolerable on every document touch, so
``AutoUpdate`` can be turned off and the object refreshed explicitly.

Whatever the extraction finds out about the *parts* is written to ``Diagnostics`` and
echoed to the Report view. A defect in a boundary part is the failure mode with no other
symptom -- the shape still draws correctly, FreeCAD still calls it valid, and the only
outward sign is a cavity that will not close -- so it has to be said out loud rather than
left for the user to infer from a volume of zero.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.cavity import (
    BooleanFailure,
    CavityError,
    DEFAULT_MINIMUM_VOLUME_MM3,
    DEFAULT_PADDING_MM,
    describe_regions,
    enclosed_regions,
    extract_regions,
    format_diagnostics,
    geometry_diagnostics,
)
from freecad.audio_analysis.checks import Severity
from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider
from freecad.audio_analysis.objects.network_objects import quantity

#: RegionIndex value meaning "every enclosed region".
ALL_ENCLOSED = -1


class AcousticCavity(AudioObject):
    """Air extracted from surrounding solids by subtraction."""

    Type = "Audio::Cavity"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyLinkList", "Boundary", "Cavity",
                "Parts that bound the air. An assembly, or the individual solids.",
            ),
            PropertySpec(
                "App::PropertyLinkList", "Caps", "Cavity",
                "Extra solids that close openings. A headphone cup is open where the ear "
                "goes, so it needs a disc across that opening before any volume is "
                "enclosed -- and that face is where the ear simulator sits.",
            ),
            PropertySpec(
                "App::PropertyLink", "Envelope", "Cavity",
                "Optional solid to subtract from. Leave empty to use a padded bounding "
                "box around the boundary parts.",
            ),
            PropertySpec(
                "App::PropertyLength", "Padding", "Cavity",
                "Stand-off of the automatic envelope from the parts",
                default=quantity(DEFAULT_PADDING_MM, "mm"),
            ),
            PropertySpec(
                "App::PropertyVolume", "MinimumVolume", "Cavity",
                "Ignore voids smaller than this -- screw holes and boolean slivers",
                default=quantity(DEFAULT_MINIMUM_VOLUME_MM3, "mm^3"),
            ),
            PropertySpec(
                "App::PropertyInteger", "RegionIndex", "Cavity",
                "Which void to keep: 0 is the largest enclosed one, -1 keeps all "
                "enclosed regions. Check Regions to see what was found.",
                default=0,
            ),
            PropertySpec(
                "App::PropertyBool", "AutoUpdate", "Cavity",
                "Re-extract on every recompute. Turn off for large assemblies, where the "
                "boolean takes seconds, and refresh explicitly instead.",
                default=True,
            ),
            PropertySpec(
                "App::PropertyString", "Regions", "Results",
                "What the last extraction found", default="not run", read_only=True,
            ),
            PropertySpec(
                "App::PropertyString", "Diagnostics", "Results",
                "Problems found in the boundary parts themselves, with what to do about "
                "them. Empty means the parts are fit to be combined.",
                default="", read_only=True,
            ),
            PropertySpec(
                "App::PropertyVolume", "Volume", "Results",
                "Volume of the kept region(s)", read_only=True,
            ),
        )

    def execute(self, obj: Any) -> None:
        if not obj.AutoUpdate:
            return
        self.extract(obj)

    # -- reporting -----------------------------------------------------------------

    @staticmethod
    def _publish(obj: Any, diagnostics: Any) -> None:
        """Write findings to the Diagnostics property and echo them to the Report view.

        Echoing matters. The property is only seen by someone who thinks to look at it,
        and the whole point of these findings is that the user has no reason to suspect
        the parts in the first place.
        """
        obj.Diagnostics = format_diagnostics(diagnostics) if diagnostics else ""
        for diagnostic in sorted(diagnostics, key=lambda d: (-d.severity, d.code)):
            text = f"Audio Analysis: {obj.Label}: {diagnostic.format()}\n"
            if diagnostic.severity is Severity.ERROR:
                FreeCAD.Console.PrintError(text)
            elif diagnostic.severity is Severity.WARNING:
                FreeCAD.Console.PrintWarning(text)
            else:
                FreeCAD.Console.PrintMessage(text)

    @staticmethod
    def _clear_result(obj: Any) -> None:
        import Part

        obj.Shape = Part.Shape()
        obj.Volume = quantity(0.0, "mm^3")

    # -- extraction ----------------------------------------------------------------

    def extract(self, obj: Any) -> None:
        """Run the extraction and set this object's Shape, Volume and Regions."""
        import Part

        if not obj.Boundary:
            obj.Regions = "no boundary parts selected"
            obj.Diagnostics = ""
            return

        parts = list(obj.Boundary) + list(obj.Caps)

        try:
            regions = extract_regions(
                obj.Boundary,
                obj.Caps,
                obj.Envelope.Shape if obj.Envelope is not None else None,
                padding=obj.Padding.getValueAs("mm").Value,
                minimum_volume=obj.MinimumVolume.getValueAs("mm^3").Value,
            )
        except BooleanFailure as exc:
            # The parts could not be combined, so no geometric verdict is available --
            # and saying "open model, add a cap" here would be a confident wrong answer.
            self._publish(obj, exc.diagnostics)
            obj.Regions = (
                "EXTRACTION FAILED -- the boundary parts could not be combined, so "
                "nothing can be concluded about whether the cavity is closed. See "
                "Diagnostics, and the Report view, for the part responsible."
            )
            self._clear_result(obj)
            return
        except CavityError as exc:
            obj.Regions = str(exc)
            obj.Diagnostics = ""
            FreeCAD.Console.PrintError(f"Audio Analysis: {obj.Label}: {exc}\n")
            return

        # The boolean held. Report anything still worth knowing about the parts: a
        # widened tolerance can be survivable here and fatal in the next model revision.
        diagnostics = geometry_diagnostics(parts)
        self._publish(obj, diagnostics)
        suspect = sorted({d.subject for d in diagnostics if d.subject})

        obj.Regions = describe_regions(
            regions, capped=bool(obj.Caps), suspect_parts=suspect
        )
        enclosed = enclosed_regions(regions)

        if not enclosed:
            self._clear_result(obj)
            if suspect:
                # The boolean ran, but on geometry known to be defective. Reporting
                # "open" here would be the same confident wrong answer, one step later.
                FreeCAD.Console.PrintWarning(
                    f"Audio Analysis: {obj.Label} found no enclosed cavity, but "
                    f"{', '.join(suspect)} was flagged first -- an open result is what a "
                    f"defective part looks like. Fix the part before reading anything "
                    f"into this.\n"
                )
                return
            hint = (
                "A cap is already supplied, so look for a gap between mating parts, or a "
                "part missing from Boundary."
                if obj.Caps
                else "Add a cap solid across the opening."
            )
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label} found no enclosed cavity. The model is "
                f"open, so its interior connects to the outside. {hint}\n"
            )
            return

        if obj.RegionIndex == ALL_ENCLOSED:
            kept = enclosed
        elif 0 <= obj.RegionIndex < len(enclosed):
            kept = [enclosed[obj.RegionIndex]]
        else:
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label}: RegionIndex {obj.RegionIndex} is out of "
                f"range; {len(enclosed)} enclosed region(s) found. Using the largest.\n"
            )
            kept = [enclosed[0]]

        shapes = [region.shape for region in kept]
        obj.Shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
        obj.Volume = quantity(sum(region.volume_mm3 for region in kept), "mm^3")


def make_cavity(doc: Any, analysis: Any = None, name: str = "Cavity") -> Any:
    """Create an AcousticCavity. A Part::FeaturePython, so it has a visible Shape."""
    obj = doc.addObject("Part::FeaturePython", name)
    AcousticCavity(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.cavity:ViewProviderAcousticCavity"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj
