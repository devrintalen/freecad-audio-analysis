"""Extracting the air from a CAD model.

The step that stands between a mechanical model and an acoustic one. What gets simulated
is the *air*, not the parts (STRUCTURE.md §6.5), and almost nobody models the air
directly -- so the workbench has to derive it.

The method is subtraction: build an envelope around the parts, fuse everything solid,
cut it away, and what remains is the void. Three things make that harder than it sounds.

**Open models have no enclosed void.** A headphone cup is open where the ear goes, so its
interior and the outside are one connected region. Closing it needs a *cap* -- a disc
across the opening -- which the user supplies as an ordinary solid. That cap is also
physically meaningful: it is where the ear simulator or baffle would sit, and it becomes
the face a probe attaches to.

**Subtraction finds every void, not just the interesting one.** Screw holes, blind
pockets and the gaps between mating parts all come back as separate regions. They are
reported with their volumes so the user can pick, and a minimum-volume filter drops the
slivers.

**Booleans fail silently, and a silent boolean failure is indistinguishable from an open
model.** This is the one that costs a day. OpenCascade will happily fuse two solids and
return a shape that is a fraction of the material it was given, still reporting
``isValid()`` true, because ``isValid()`` only checks that the topology is
self-consistent. The subtraction then removes almost nothing, the exterior comes back as
the only region, and the verdict reads "OPEN MODEL -- add a cap" for a model that is
sealed and already capped. So the union is checked against an invariant it cannot escape
-- a union is never smaller than its largest part, nor larger than the sum of them -- and
when that trips, the extraction refuses to draw any geometric conclusion at all and goes
looking for the part responsible instead. See :func:`fuse_diagnostic` and
:func:`geometry_diagnostics`.

Region 0 is always the largest enclosed void. The exterior -- the region that reaches the
envelope boundary -- is identified and excluded rather than silently returned as if it
were a cavity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import FreeCAD
import Part

from freecad.audio_analysis.checks import Diagnostic, Severity

#: Ignore boolean slivers below this volume, in mm^3.
DEFAULT_MINIMUM_VOLUME_MM3 = 1.0

#: How far the automatic envelope stands off the parts, in mm.
DEFAULT_PADDING_MM = 2.0

#: A region whose bounding box comes within this of the envelope's is treated as the
#: exterior. Generous, because the exterior wraps the parts and always reaches the edge.
EXTERIOR_TOLERANCE_MM = 1e-6

#: Above this shape tolerance, in mm, a part is reported as unfit for booleans.
#:
#: OpenCascade's default is 1e-7 mm. A part far above that has been through an operation
#: that could not close a surface exactly and widened its tolerance until the result
#: appeared to fit. One micron of geometric fuzz is acoustically nothing -- the viscous
#: boundary layer at 1 kHz is seventy times larger -- so this threshold is purely about
#: numerics, and it is set well above the 1e-5 mm that ordinary filleted parts carry.
SUSPECT_TOLERANCE_MM = 1e-3

#: Fractional slack on the union invariant. A union's volume is bounded exactly; this
#: only absorbs floating-point noise in the volume integration itself.
FUSE_VOLUME_SLACK = 1e-6


class CavityError(ValueError):
    """Raised when a cavity cannot be extracted."""


class BooleanFailure(CavityError):
    """The union of the boundary parts is not trustworthy, so nothing can be concluded.

    Carries the :class:`~freecad.audio_analysis.checks.Diagnostic` list that explains
    which part is responsible, so callers can render it rather than re-deriving it.
    """

    def __init__(self, diagnostics: Sequence[Diagnostic]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__(format_diagnostics(self.diagnostics))


def format_diagnostics(diagnostics: Sequence[Diagnostic]) -> str:
    """Render diagnostics worst-first for a report field or the console."""
    ordered = sorted(diagnostics, key=lambda d: (-d.severity, d.code))
    return "\n".join(d.format() for d in ordered)


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


@dataclass(frozen=True)
class BoundarySolid:
    """One solid, still tied to the object it came from.

    Kept as a pair because a fault has to be *named*. "The boolean failed" sends someone
    hunting through an assembly; "Cushion fails the boolean check" does not.
    """

    label: str
    solid: Any


def collect_boundary_solids(
    objects: Sequence[Any], minimum_volume: float = 1e-6
) -> list[BoundarySolid]:
    """Every valid solid carried by ``objects``, labelled with its source object.

    Datum planes and joint objects carry null or nonsense shapes -- an unbounded plane
    reports an absurd volume and makes a subsequent boolean fail outright with "Null
    shape" -- so they are filtered here rather than at the point of use.
    """
    found: list[BoundarySolid] = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        label = getattr(obj, "Label", None) or getattr(obj, "Name", "part")
        solids = [s for s in shape.Solids if s.isValid() and s.Volume > minimum_volume]
        for index, solid in enumerate(solids):
            name = label if len(solids) == 1 else f"{label} (solid {index + 1})"
            found.append(BoundarySolid(name, solid))
    return found


def collect_solids(objects: Sequence[Any], minimum_volume: float = 1e-6) -> list[Any]:
    """Every valid solid carried by ``objects``, with placements applied."""
    return [s.solid for s in collect_boundary_solids(objects, minimum_volume)]


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


# ---------------------------------------------------------------------------------
# Telling the user *why* an extraction failed.
#
# Two checks, deliberately split by cost. The tolerance scan and the volume invariant
# together take a few milliseconds on a full assembly and so run every time. The
# OpenCascade boolean-operation check takes of the order of a second per detailed part --
# as long as the fuse itself -- so it runs only once something has already gone wrong,
# to name the culprit.
# ---------------------------------------------------------------------------------


def _bop_failure(solid: Any) -> str | None:
    """``None`` if ``solid`` passes OCC's boolean-operation check, else a summary.

    ``Shape.check(True)`` raises with one line per offending sub-shape; a real fault
    produces dozens, so they are counted by kind rather than repeated.
    """
    try:
        solid.check(True)
    except Exception as exc:  # noqa: BLE001 -- OCC reports these only as text
        kinds: dict[str, int] = {}
        for line in str(exc).splitlines():
            line = line.strip()
            if line.startswith("Error in"):
                kinds[line] = kinds.get(line, 0) + 1
        if not kinds:
            return str(exc).strip() or "failed the boolean check"
        return ", ".join(f"{count}x {kind}" for kind, count in sorted(kinds.items()))
    return None


def tolerance_diagnostic(source: BoundarySolid) -> Diagnostic | None:
    """Flag a part whose geometric tolerance has been widened past reason."""
    tolerance = source.solid.getTolerance(0)
    if tolerance <= SUSPECT_TOLERANCE_MM:
        return None

    worst = max(source.solid.Vertexes, key=lambda v: v.Tolerance, default=None)
    where = ""
    if worst is not None and worst.Tolerance > SUSPECT_TOLERANCE_MM:
        point = worst.Point
        where = (
            f" The worst is a vertex at ({point.x:.2f}, {point.y:.2f}, {point.z:.2f}) "
            f"carrying {worst.Tolerance:.3g} mm."
        )

    return Diagnostic(
        severity=Severity.WARNING,
        code="part-tolerance-widened",
        message=f"Geometric tolerance is {tolerance:.3g} mm.",
        why=(
            "OpenCascade's default is 1e-7 mm, so this part is 'fuzzy' at a scale "
            f"{tolerance / 1e-7:.0f} times larger than it should be. That happens when an "
            "operation could not close a surface exactly and widened the tolerance until "
            "the result appeared to fit -- a sweep along a closed path whose start and end "
            "sections differ is a common cause. A vertex with a large tolerance swallows "
            "the faces near it, which is what makes a later boolean fail or return "
            "nonsense." + where
        ),
        remedy=(
            "Open the source model and run Part -> Check geometry on this part. If it "
            "reports self-intersections, rebuild the feature that made them rather than "
            "trying to repair the result: refine, sew and shape-fixing all leave the "
            "tolerance where it is."
        ),
        reference="STRUCTURE.md §6.5",
        subject=source.label,
    )


def boolean_check_diagnostic(source: BoundarySolid) -> Diagnostic | None:
    """Flag a part that OCC's boolean-operation check rejects. Slow; see module notes."""
    failure = _bop_failure(source.solid)
    if failure is None:
        return None

    return Diagnostic(
        severity=Severity.ERROR,
        code="part-fails-boolean-check",
        message=f"Fails OpenCascade's boolean-operation check: {failure}.",
        why=(
            "FreeCAD's ordinary validity test only checks that the topology is "
            "self-consistent -- faces meet edges, edges meet vertices -- and this part "
            "passes that. Booleans need more: no face may intersect another, and no vertex "
            "may carry a tolerance wide enough to swallow its neighbours. A part can look "
            "perfect in the 3D view, report itself valid, and still make every union and "
            "subtraction it takes part in silently wrong."
        ),
        remedy=(
            "Fix this part in its own document before extracting the cavity. Part -> Check "
            "geometry names the offending faces. The fix belongs in the feature that "
            "created them, not in a repair applied afterwards."
        ),
        reference="STRUCTURE.md §6.5",
        subject=source.label,
    )


def geometry_diagnostics(
    objects: Sequence[Any], *, deep: bool = False
) -> list[Diagnostic]:
    """Findings about the boundary parts themselves, before any boolean is attempted.

    The default pass is cheap enough to run on every recompute. ``deep`` adds the
    OpenCascade boolean-operation check, which is thorough and costs about a second per
    detailed part, so it is reserved for the case where something has already failed.
    """
    return diagnostics_for_solids(collect_boundary_solids(objects), deep=deep)


def diagnostics_for_solids(
    sources: Sequence[BoundarySolid], *, deep: bool = False
) -> list[Diagnostic]:
    """The same findings, for solids a caller has already collected and labelled."""
    diagnostics: list[Diagnostic] = []
    for source in sources:
        tolerance = tolerance_diagnostic(source)
        if tolerance is not None:
            diagnostics.append(tolerance)
        if deep:
            failure = boolean_check_diagnostic(source)
            if failure is not None:
                diagnostics.append(failure)
    return diagnostics


def fuse_diagnostic(
    sources: Sequence[BoundarySolid], fused: Any
) -> Diagnostic | None:
    """Check the union against the invariant no correct union can escape.

    A union is never smaller than its largest part, and never larger than the sum of the
    parts. Both bounds are exact, which makes this a free and completely reliable trip-
    wire: the alternative is trusting a shape that reports itself valid while containing a
    fraction of the material it was built from.
    """
    if not sources:
        return None

    total = sum(s.solid.Volume for s in sources)
    largest = max(s.solid.Volume for s in sources)

    if fused is None or fused.isNull() or not fused.Solids:
        got = "an empty shape"
        volume = 0.0
    else:
        volume = fused.Volume
        got = f"{volume / 1000.0:.3f} cm3"

    slack = FUSE_VOLUME_SLACK * max(total, 1.0)
    if largest - slack <= volume <= total + slack:
        return None

    return Diagnostic(
        severity=Severity.ERROR,
        code="fuse-failed",
        message=(
            f"Fusing the {len(sources)} boundary part(s) produced {got}, which is "
            f"impossible."
        ),
        why=(
            f"A union is never smaller than its largest part ({largest / 1000.0:.3f} cm3) "
            f"nor larger than the sum of them ({total / 1000.0:.3f} cm3). This result is "
            "outside both bounds, so the boolean failed -- but it still reports itself as "
            "a valid solid, so nothing raised. Every conclusion downstream would be drawn "
            "from geometry that does not exist: the subtraction would remove almost "
            "nothing, the exterior would come back as the only region, and the model would "
            "be reported as open when it may well be sealed."
        ),
        remedy=(
            "One of the boundary parts is unfit for boolean operations; the findings "
            "below name it. Fix it in its source document and extract again."
        ),
        reference="STRUCTURE.md §6.5",
    )


def cut_failure_diagnostic(exc: Exception) -> Diagnostic:
    """The subtraction threw. Explain it in terms of the parts, not of OpenCascade.

    The volume invariant is necessary but not sufficient: a union can land inside its
    bounds and still be malformed enough that the subtraction fails outright. That is the
    other half of the same defect, so it gets the same treatment -- name the part rather
    than advising the user to "refine the selection", which is not something they can act
    on.
    """
    return Diagnostic(
        severity=Severity.ERROR,
        code="cut-failed",
        message=f"Subtracting the parts from the envelope failed: {exc}.",
        why=(
            "The parts fused to something whose volume looked plausible, so the union "
            "passed its bounds check, but the shape is malformed enough that the "
            "subtraction could not run at all. A part that is unfit for booleans does not "
            "fail the same way twice: sometimes it returns a wrong answer quietly, "
            "sometimes it stops the operation dead."
        ),
        remedy=(
            "This is a defect in one of the boundary parts, not in the selection. The "
            "findings below name it."
        ),
        reference="STRUCTURE.md §6.5",
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

    Raises :class:`BooleanFailure` if the union of the parts is not trustworthy, rather
    than reporting a geometric verdict computed from a broken shape.
    """
    parts = list(boundary) + list(caps)
    sources = collect_boundary_solids(parts)
    if not sources:
        raise CavityError(
            "no solids found in the selected objects. Cavity extraction needs the parts "
            "that bound the air -- datum planes and joints carry no volume."
        )
    return extract_regions_from_solids(
        sources, envelope, padding=padding, minimum_volume=minimum_volume
    )


def extract_regions_from_solids(
    sources: Sequence[BoundarySolid],
    envelope: Any = None,
    *,
    padding: float = DEFAULT_PADDING_MM,
    minimum_volume: float = DEFAULT_MINIMUM_VOLUME_MM3,
) -> list[CavityRegion]:
    """The same extraction, but from solids that have already been collected and placed.

    Split out because a solid's placement and the object it belongs to cannot both be
    recovered from a container's flattened ``Shape``. A caller that has walked an assembly
    itself -- :mod:`~freecad.audio_analysis.seeding` does -- arrives holding labelled,
    correctly placed solids, and re-deriving them from objects here would throw that away
    and reintroduce the local-coordinates trap it just avoided.
    """
    if not sources:
        raise CavityError(
            "no solids found in the selected objects. Cavity extraction needs the parts "
            "that bound the air -- datum planes and joints carry no volume."
        )

    solids = [s.solid for s in sources]
    fused = solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]

    broken = fuse_diagnostic(sources, fused)
    if broken is not None:
        # Only now pay for the expensive per-part check: it is what names the culprit.
        raise BooleanFailure([broken] + diagnostics_for_solids(sources, deep=True))

    shell = envelope if envelope is not None else make_envelope(solids, padding)

    try:
        void = shell.cut(fused)
    except Exception as exc:  # noqa: BLE001 -- OCC boolean failures are opaque
        raise BooleanFailure(
            [cut_failure_diagnostic(exc)] + diagnostics_for_solids(sources, deep=True)
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

#: What to suggest when a model is open. Split by whether a cap was already supplied,
#: because telling someone to add the cap they just added is how a tool loses its
#: credibility on the one message that most needed to be trusted.
_ADD_A_CAP = (
    "Add a cap solid across the opening; for a headphone that is where the ear simulator "
    "sits."
)
_CAP_DID_NOT_CLOSE_IT = (
    "A cap is already supplied, so the remaining path is elsewhere: either the cap does "
    "not reach the parts it should seal against, or a part that bounds the air is missing "
    "from Boundary, or there is a genuine gap between two mating parts. A gap may well be "
    "real -- leakage dominates headphone bass -- in which case model it as a LeakPath "
    "rather than closing it."
)


def describe_regions(
    regions: Sequence[CavityRegion],
    *,
    capped: bool = False,
    suspect_parts: Sequence[str] = (),
) -> str:
    """A listing for the report view, led by a verdict.

    The verdict comes first deliberately. A real assembly yields a dozen sealed pockets
    from screw holes and fit gaps, and if the listing simply enumerates them the one fact
    that matters -- that the cavity the user wanted is *not* enclosed -- ends up below the
    fold, looking like success.

    ``capped`` says whether the caller supplied any cap solids, which decides what the
    remedy can honestly be.

    ``suspect_parts`` names boundary parts that were flagged before the boolean ran. A
    union can pass its bounds check, subtract cleanly, and still be wrong, so when a part
    is known to be defective an "open" verdict is withdrawn rather than reported: the
    likeliest explanation for it is the defect, not the geometry.
    """
    if not regions:
        return "No regions found."

    enclosed = enclosed_regions(regions)
    exterior = [r for r in regions if r.is_exterior]
    largest_exterior = max((r.volume_mm3 for r in exterior), default=0.0)
    biggest_enclosed = max((r.volume_mm3 for r in enclosed), default=0.0)
    advice = _CAP_DID_NOT_CLOSE_IT if capped else _ADD_A_CAP

    lines: list[str] = []
    if not enclosed and suspect_parts:
        lines.append(
            f"NO VERDICT -- no enclosed cavity was found, but {', '.join(suspect_parts)} "
            f"was flagged before the boolean ran, and a part in that state makes unions "
            f"and subtractions return nonsense. An open result is what a broken part looks "
            f"like, so this is not evidence that the model is open. Fix the part named in "
            f"Diagnostics, then extract again."
        )
    elif not enclosed:
        lines.append(
            "OPEN MODEL -- no enclosed cavity. The interior and the outside are one "
            "region, so there is no volume to measure yet. " + advice
        )
    elif largest_exterior and biggest_enclosed < INCIDENTAL_FRACTION * largest_exterior:
        lines.append(
            f"LIKELY OPEN -- the {len(enclosed)} enclosed region(s) are all tiny "
            f"(largest {biggest_enclosed / 1000.0:.3f} cm3), which is the signature of "
            f"screw holes and fit gaps rather than a cavity. The main interior is "
            f"connected to the outside. " + advice
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
