"""The AcousticCap document object: a plug for an opening, tied to the edge it came from.

A ``Part::FeaturePython``, so the cap is a real solid in the 3D view and can be handed
straight to :class:`~freecad.audio_analysis.objects.cavity_object.AcousticCavity` as a cap.

Parametric on purpose. The alternative -- a one-shot solid dropped into the document --
goes stale the moment a port is resized, and it goes stale *silently*: the cavity still
extracts, the volume still looks plausible, and nothing announces that the cap no longer
matches the hole. Keeping the reference means the cap follows the design, which is the same
argument STRUCTURE.md §6.7 makes for reading element areas from geometry rather than
typing them.

``OpeningArea`` is the property worth knowing about. It is the area of the loop itself,
measured before the cap outline is grown, so it is the honest open area of the port --
exactly the number a ``Port`` or ``AcousticResistance`` needs. Capping an opening and then
declaring it open in the network is not a contradiction; it is the whole workflow.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.capping import (
    CapError,
    DEFAULT_OVERLAP_MM,
    DEFAULT_THICKNESS_MM,
    build_caps,
    describe_openings,
)
from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider
from freecad.audio_analysis.objects.network_objects import quantity


class AcousticCap(AudioObject):
    """A solid that closes an opening so a cavity can be extracted from it."""

    Type = "Audio::Cap"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                # XLink, not Link: in an assembly the parts are App::Links into separate
                # documents, and a plain PropertyLinkSubList refuses an object it does not
                # own with "does not support external object". Every reference this
                # workbench takes on a real assembly is external, so Link is simply the
                # wrong property here.
                "App::PropertyXLinkSubList", "Opening", "Cap",
                "One edge on the rim of the opening -- the rest of the loop is found "
                "from it. A face works too, and several edges cap several openings.",
            ),
            PropertySpec(
                "App::PropertyLength", "Thickness", "Cap",
                "Total depth of the plug, straddling the opening: half sits either side, "
                "so the cap crosses the surface whichever way the part faces.",
                default=quantity(DEFAULT_THICKNESS_MM, "mm"),
            ),
            PropertySpec(
                "App::PropertyLength", "Overlap", "Cap",
                "How far the cap outline is grown past the opening, so it overlaps the "
                "surrounding material instead of only touching it. Two solids meeting "
                "along a curve are what booleans handle worst.",
                default=quantity(DEFAULT_OVERLAP_MM, "mm"),
            ),
            PropertySpec(
                "App::PropertyBool", "Propagate", "Cap",
                "Expand from the picked edge to the whole loop, as fillet does. Turn off "
                "to use only the edges actually selected.",
                default=True,
            ),
            PropertySpec(
                "App::PropertyString", "Openings", "Results",
                "What the loop search found", default="not run", read_only=True,
            ),
            PropertySpec(
                "App::PropertyArea", "OpeningArea", "Results",
                "Open area of the loop, measured before Overlap is added. This is the "
                "port area: give it to a Port if the opening is meant to stay open.",
                read_only=True,
            ),
        )

    def execute(self, obj: Any) -> None:
        self.build(obj)

    def build(self, obj: Any) -> None:
        """Rebuild the cap from its referenced opening."""
        import Part

        if not obj.Opening:
            obj.Openings = "no opening referenced"
            obj.Shape = Part.Shape()
            obj.OpeningArea = quantity(0.0, "mm^2")
            return

        try:
            shape, openings = build_caps(
                obj.Opening,
                thickness=obj.Thickness.getValueAs("mm").Value,
                overlap=obj.Overlap.getValueAs("mm").Value,
                propagate=bool(obj.Propagate),
            )
        except CapError as exc:
            obj.Openings = f"FAILED -- {exc}"
            obj.Shape = Part.Shape()
            obj.OpeningArea = quantity(0.0, "mm^2")
            FreeCAD.Console.PrintError(f"Audio Analysis: {obj.Label}: {exc}\n")
            return

        obj.Shape = shape
        obj.Openings = describe_openings(openings)
        obj.OpeningArea = quantity(sum(o.area_mm2 for o in openings), "mm^2")

        # Flattening a gently contoured rim is a detail. Flattening a deeply warped one is
        # a modelling decision, and only the user can say whether that plane is where the
        # acoustic boundary belongs.
        for opening in (o for o in openings if o.badly_out_of_plane):
            FreeCAD.Console.PrintWarning(
                f"Audio Analysis: {obj.Label}: {opening.label} departs from its best-fit "
                f"plane by +/-{opening.flatness_mm:.2f} mm across an opening of "
                f"{opening.equivalent_radius_mm:.1f} mm equivalent radius, and has been "
                f"capped flat. That is the right boundary for an ear plane, but check it "
                f"is where you meant the domain to end.\n"
            )


def make_cap(doc: Any, analysis: Any = None, name: str = "Cap") -> Any:
    """Create an AcousticCap. A Part::FeaturePython, so it has a visible Shape."""
    obj = doc.addObject("Part::FeaturePython", name)
    AcousticCap(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.cap:ViewProviderAcousticCap"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj
