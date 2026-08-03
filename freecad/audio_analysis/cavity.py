"""Extracting the air from a CAD model.

The step that stands between a mechanical model and an acoustic one. What gets simulated
is the *air*, not the parts (STRUCTURE.md §6.5), and almost nobody models the air
directly -- so the workbench has to derive it.

The method is subtraction: build an envelope around the parts, fuse everything solid,
cut it away, and what remains is the void. Two things make that harder than it sounds.

**Open models have no enclosed void.** A headphone cup is open where the ear goes, so its
interior and the outside are one connected region. Closing it needs a *cap* -- a disc
across the opening -- which the user supplies as an ordinary solid. That cap is also
physically meaningful: it is where the ear simulator or baffle would sit, and it becomes
the face a probe attaches to.

**Subtraction finds every void, not just the interesting one.** Screw holes, blind
pockets and the gaps between mating parts all come back as separate regions. They are
reported with their volumes so the user can pick, and a minimum-volume filter drops the
slivers.

Region 0 is always the largest enclosed void. The exterior -- the region that reaches the
envelope boundary -- is identified and excluded rather than silently returned as if it
were a cavity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import FreeCAD
import Part

#: Ignore boolean slivers below this volume, in mm^3.
DEFAULT_MINIMUM_VOLUME_MM3 = 1.0

#: How far the automatic envelope stands off the parts, in mm.
DEFAULT_PADDING_MM = 2.0

#: A region whose bounding box comes within this of the envelope's is treated as the
#: exterior. Generous, because the exterior wraps the parts and always reaches the edge.
EXTERIOR_TOLERANCE_MM = 1e-6


class CavityError(ValueError):
    """Raised when a cavity cannot be extracted."""


@dataclass(frozen=True)
class CavityRegion:
    """One connected void found by the extraction."""

    shape: Any
    volume_mm3: float
    is_exterior: bool

    @property
    def volume_cm3(self) -> float:
        return self.volume_mm3 / 1000.0

    def describe(self, index: int) -> str:
        bb = self.shape.BoundBox
        kind = "exterior" if self.is_exterior else "enclosed"
        return (
            f"[{index}] {self.volume_cm3:.3f} cm3 ({kind}), "
            f"bbox {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm"
        )


def collect_solids(objects: Sequence[Any], minimum_volume: float = 1e-6) -> list[Any]:
    """Every valid solid carried by ``objects``, with placements applied.

    Datum planes and joint objects carry null or nonsense shapes -- an unbounded plane
    reports an absurd volume and makes a subsequent boolean fail outright with "Null
    shape" -- so they are filtered here rather than at the point of use.
    """
    solids: list[Any] = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        for solid in shape.Solids:
            if solid.isValid() and solid.Volume > minimum_volume:
                solids.append(solid)
    return solids


def make_envelope(solids: Sequence[Any], padding: float = DEFAULT_PADDING_MM) -> Any:
    """A padded bounding box around ``solids``.

    Padding matters: without a gap between the parts and the envelope wall, the exterior
    void degenerates and cannot be told apart from an enclosed one.
    """
    if not solids:
        raise CavityError("nothing to build an envelope around")
    if padding <= 0.0:
        raise CavityError(f"padding must be positive, got {padding} mm")

    box = solids[0].BoundBox
    for solid in solids[1:]:
        box.add(solid.BoundBox)
    return Part.makeBox(
        box.XLength + 2 * padding,
        box.YLength + 2 * padding,
        box.ZLength + 2 * padding,
        FreeCAD.Vector(box.XMin - padding, box.YMin - padding, box.ZMin - padding),
    )


def extract_regions(
    boundary: Sequence[Any],
    caps: Sequence[Any] = (),
    envelope: Any = None,
    *,
    padding: float = DEFAULT_PADDING_MM,
    minimum_volume: float = DEFAULT_MINIMUM_VOLUME_MM3,
) -> list[CavityRegion]:
    """Find every void enclosed by ``boundary`` (plus ``caps``), largest enclosed first.

    ``boundary`` and ``caps`` are document objects. Caps are ordinary solids the user
    models to close an opening -- a disc across a cup's ear side, say.

    Returns regions sorted with enclosed voids first by descending volume, then the
    exterior. An exterior region always exists unless a custom ``envelope`` is used that
    the parts fill completely.
    """
    solids = collect_solids(list(boundary) + list(caps))
    if not solids:
        raise CavityError(
            "no solids found in the selected objects. Cavity extraction needs the parts "
            "that bound the air -- datum planes and joints carry no volume."
        )

    fused = solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]
    shell = envelope if envelope is not None else make_envelope(solids, padding)

    try:
        void = shell.cut(fused)
    except Exception as exc:  # noqa: BLE001 -- OCC boolean failures are opaque
        raise CavityError(
            f"the subtraction failed ({exc}). This usually means one of the solids is "
            f"invalid; try refining the selection."
        ) from exc

    envelope_box = shell.BoundBox
    regions: list[CavityRegion] = []
    for solid in void.Solids:
        if solid.Volume < minimum_volume:
            continue
        box = solid.BoundBox
        # The exterior is the region that reaches the envelope wall on every axis.
        is_exterior = (
            abs(box.XLength - envelope_box.XLength) < EXTERIOR_TOLERANCE_MM
            and abs(box.YLength - envelope_box.YLength) < EXTERIOR_TOLERANCE_MM
            and abs(box.ZLength - envelope_box.ZLength) < EXTERIOR_TOLERANCE_MM
        )
        regions.append(CavityRegion(solid, solid.Volume, is_exterior))

    regions.sort(key=lambda r: (r.is_exterior, -r.volume_mm3))
    return regions


def enclosed_regions(regions: Sequence[CavityRegion]) -> list[CavityRegion]:
    """Only the regions that are genuinely enclosed."""
    return [r for r in regions if not r.is_exterior]


#: An enclosed region smaller than this fraction of the exterior is almost certainly a
#: screw hole or a fit gap rather than the cavity the user is after.
INCIDENTAL_FRACTION = 0.01

#: How many regions to list before truncating.
MAX_LISTED_REGIONS = 12


def describe_regions(regions: Sequence[CavityRegion]) -> str:
    """A listing for the report view, led by a verdict.

    The verdict comes first deliberately. A real assembly yields a dozen sealed pockets
    from screw holes and fit gaps, and if the listing simply enumerates them the one fact
    that matters -- that the cavity the user wanted is *not* enclosed -- ends up below the
    fold, looking like success.
    """
    if not regions:
        return "No regions found."

    enclosed = enclosed_regions(regions)
    exterior = [r for r in regions if r.is_exterior]
    largest_exterior = max((r.volume_mm3 for r in exterior), default=0.0)
    biggest_enclosed = max((r.volume_mm3 for r in enclosed), default=0.0)

    lines: list[str] = []
    if not enclosed:
        lines.append(
            "OPEN MODEL -- no enclosed cavity. The interior and the outside are one "
            "region, so there is no volume to measure yet. Add a cap solid across the "
            "opening; for a headphone that is where the ear simulator sits."
        )
    elif largest_exterior and biggest_enclosed < INCIDENTAL_FRACTION * largest_exterior:
        lines.append(
            f"LIKELY OPEN -- the {len(enclosed)} enclosed region(s) are all tiny "
            f"(largest {biggest_enclosed / 1000.0:.3f} cm3), which is the signature of "
            f"screw holes and fit gaps rather than a cavity. The main interior is "
            f"connected to the outside; add a cap solid across the opening."
        )
    else:
        lines.append(
            f"{len(enclosed)} enclosed region(s); largest "
            f"{biggest_enclosed / 1000.0:.3f} cm3."
        )

    for index, region in enumerate(regions[:MAX_LISTED_REGIONS]):
        lines.append(region.describe(index))
    if len(regions) > MAX_LISTED_REGIONS:
        lines.append(f"... and {len(regions) - MAX_LISTED_REGIONS} more")
    return "\n".join(lines)
