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

from freecad.audio_analysis import seeding
from freecad.audio_analysis.cavity import (
    BooleanFailure,
    CavityError,
    DEFAULT_MINIMUM_VOLUME_MM3,
    DEFAULT_PADDING_MM,
    describe_regions,
    enclosed_regions,
    extract_regions_from_solids,
    format_diagnostics,
    geometry_diagnostics,
)
from freecad.audio_analysis.checks import Severity
from freecad.audio_analysis.seeding import SeedError
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
                # XLinkSub for the same reason the cap's Opening is: in an assembly the
                # parts are links into other documents, and a plain link property refuses
                # them outright.
                "App::PropertyXLinkSub", "Seed", "Cavity",
                "One face, edge or vertex on the air side of any bounding part. The "
                "cavity kept is whichever void touches it, which survives a rebuild that "
                "renumbers the regions -- unlike RegionIndex. A face is best: it says "
                "which side the air is on, so it is never ambiguous.",
            ),
            PropertySpec(
                "App::PropertyInteger", "RegionIndex", "Cavity",
                "Which void to keep when no Seed is set: 0 is the largest enclosed one, "
                "-1 keeps all enclosed regions. Check Regions to see what was found.",
                default=0,
            ),
            PropertySpec(
                "App::PropertyBool", "IncludeHidden", "Cavity",
                "Include bodies that are hidden in the 3D view. On by default: a part "
                "still bounds the air whether or not anyone is looking at it. Caps are "
                "included whatever this says, since a cap is routinely hidden once it "
                "works and dropping one silently reopens the cavity.",
                default=True,
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
                "App::PropertyString", "BoundedBy", "Results",
                "Which parts actually bound the kept cavity, and the share of its wall "
                "each one carries. Derived from the result, so it cannot disagree with "
                "the geometry -- a part contributing a fraction of a percent is usually a "
                "screw that has nothing to do with the acoustics.",
                default="", read_only=True,
            ),
            PropertySpec(
                "App::PropertyVolume", "Volume", "Results",
                "Volume of the kept region(s)", read_only=True,
            ),
        )

    #: Properties this object used to carry. Removed on restore so a document written by
    #: an older workbench does not keep showing a control that does nothing.
    OBSOLETE_PROPERTIES = ("MaxOpening",)

    def execute(self, obj: Any) -> None:
        if not obj.AutoUpdate:
            return
        self.extract(obj)

    def onDocumentRestored(self, obj: Any) -> None:
        super().onDocumentRestored(obj)
        for name in self.OBSOLETE_PROPERTIES:
            if hasattr(obj, name):
                try:
                    obj.removeProperty(name)
                except Exception:  # noqa: BLE001 -- a leftover control is not worth raising
                    pass

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

        # Expanding the containers here rather than taking their flattened Shape is what
        # keeps each solid tied to the part it came from, so BoundedBy can name names --
        # and it is the only way IncludeHidden can mean anything, since a container's own
        # shape has already dropped its hidden children by the time we see it.
        sources, hidden = seeding.solids_for(
            parts, include_hidden=bool(getattr(obj, "IncludeHidden", True))
        )

        try:
            regions = extract_regions_from_solids(
                sources,
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
        if hidden:
            obj.Regions += (
                f"\nSkipped as hidden: {', '.join(sorted(set(hidden)))}. These bound the "
                f"air whether or not they are shown; turn IncludeHidden on to use them."
            )
        enclosed = enclosed_regions(regions)

        # A seed picks the region by where it is, not by what number it came out as, so it
        # can legitimately land on the exterior. That is not a failure to report as "no
        # cavity" -- it is the single most useful thing this object can say, because a
        # cavity that reaches the envelope wall means a cap is missing or a leak path was
        # overlooked. Show it, and say so.
        probe = self._probe(obj)
        if probe is not None:
            self._keep_seeded(obj, regions, probe, sources)
            return

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

        self._keep(obj, kept, sources)

    # -- choosing which region to keep ----------------------------------------------

    @staticmethod
    def _probe(obj: Any) -> Any:
        """The seed reduced to a probe point, or ``None`` if there is no usable seed."""
        seed = getattr(obj, "Seed", None)
        if not seed:
            return None
        source, subnames = seed
        subname = next((s for s in subnames if s), None)
        if source is None or not subname:
            return None
        try:
            return seeding.probe_from_reference(source, subname)
        except (SeedError, CavityError) as exc:
            # A seed goes stale when the feature it names is rebuilt. Falling back to
            # RegionIndex would quietly keep a different cavity, so say what happened.
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label}: the seed pick could not be resolved "
                f"({exc}). Re-pick it, or clear Seed to select by RegionIndex.\n"
            )
            return None

    def _keep_seeded(self, obj: Any, regions: Any, probe: Any, sources: Any) -> None:
        """Keep the region the probe sits in, whatever kind of region that turns out."""
        region = seeding.region_for_probe(regions, probe)
        if region is None:
            self._clear_result(obj)
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label}: the seeded face touches no air. It is "
                f"probably buried against another part -- pick a face on the side the "
                f"cavity is on.\n"
            )
            return

        self._keep(obj, [region], sources)

        if region.is_exterior:
            obj.Regions = (
                f"LEAKS TO OUTSIDE -- the seeded pick is on a region of "
                f"{region.volume_cm3:.3f} cm3 that reaches the envelope wall, so this air "
                f"is continuous with the outside and is not a cavity. An opening of "
                f"any size does this, however small -- closing one means capping it.\n"
                + obj.Regions
            )
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label} leaks to the outside. The cavity fills the "
                f"whole envelope, which means a cap is missing or a leak path was "
                f"overlooked. BoundedBy lists every part it reaches.\n"
            )

    def _keep(self, obj: Any, kept: Any, sources: Any) -> None:
        """Set Shape, Volume and BoundedBy from the regions being kept."""
        import Part

        shapes = [region.shape for region in kept]
        obj.Shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
        obj.Volume = quantity(sum(region.volume_mm3 for region in kept), "mm^3")

        try:
            described = []
            for region in kept:
                parts, unattributed = seeding.wetted_parts(region, sources)
                described.append(seeding.describe_wetted(parts, unattributed))
            obj.BoundedBy = "\n".join(described)
        except Exception as exc:  # noqa: BLE001 -- reporting must never break the result
            obj.BoundedBy = f"could not be determined: {exc}"


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
