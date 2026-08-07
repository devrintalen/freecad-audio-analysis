"""Finding a cavity from one pick, instead of from a list of parts.

The old way to extract a cavity was to select every solid that bounds the air. On the
two-way cup that is twelve parts plus nine caps, each of which has to be selected *whole* --
picking a face instead of the body silently contributes nothing -- and forgetting one
produces an open model with no hint as to which one was missed. The tedium is not the real
cost; the real cost is that a missed part looks exactly like a leak.

So the direction is inverted. The user picks **one face, edge or vertex** on the air side of
any part, and the geometry answers the rest:

1. Collect every solid in scope -- the assembly the pick came from, or the document's root
   solids for a single part -- plus every cap, and subtract them from an envelope
   (:mod:`~freecad.audio_analysis.cavity` does this part).
2. Turn the pick into a **probe point** just off the surface, on the air side.
3. Keep the void region that contains that point.
4. Report which solids actually bound it, and how much of the wetted wall each contributes.

Step 4 is what replaces the manual selection. The parts that bound the cavity are a
*result* of the extraction, not an input to it, so they cannot be got wrong -- and the
share of wetted area each one carries is worth showing, because a part contributing 0.2%
is usually a screw that has nothing to do with the acoustics.

**Why the probe point sits off the surface.** A picked face lies exactly on the boundary
between a solid and the void, so a point on it is equally "in" both and every region test
is a coin toss. Offsetting along the face normal, away from the material, lands the point
unambiguously in the air. Edges and vertices have no single normal -- an edge is shared by
two faces, a vertex by three or more -- so those fall back to nearest-region matching,
which is why picking a face is worth recommending in the UI.

**Placements are load-bearing here.** A child of an assembly reports its ``Shape`` in its
own frame, so a part sitting at the assembly's ``x = 50`` still reports ``XMin = 0``
(STRUCTURE.md §6.5). Collecting child shapes directly would scatter the parts back to the
origin and produce a cavity that is pure nonsense while still looking like a plausible
solid. Everything here goes through ``getSubObject``, which returns the shape already
placed in the frame the user is looking at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import FreeCAD
import Part

from freecad.audio_analysis.cavity import BoundarySolid, CavityError, CavityRegion

#: How far off the surface the probe point sits, in mm.
#:
#: Small enough to stay inside a narrow channel -- a 0.2 mm vent slot still has room --
#: and large enough to clear the surface tolerance of an ordinary part, which sits around
#: 1e-5 mm. Nothing acoustic depends on this; it only has to pick a side.
PROBE_OFFSET_MM = 0.01

#: Tolerance for "this point lies on that solid's surface", in mm. Used when attributing
#: the cavity's wall area back to the parts that bound it. Generous relative to OCC's
#: 1e-7 mm default because the void's faces come from a boolean and carry its tolerance.
ON_SURFACE_TOLERANCE_MM = 1e-3

#: Proxy class names whose objects represent *air*, not material. Feeding a cavity back in
#: as a boundary part would subtract the air from itself.
AIR_PROXIES = ("AcousticCavity", "AcousticVolume")

#: Proxy class name for a cap. Caps are included whatever their visibility -- a cap is a
#: modelling device rather than something anyone wants to look at, so it is routinely
#: hidden once it works, and dropping it would reopen the cavity it was made to close.
CAP_PROXY = "AcousticCap"


class SeedError(CavityError):
    """Raised when a pick cannot be turned into a usable probe."""


@dataclass(frozen=True)
class SeedProbe:
    """A pick, reduced to a point that is unambiguously in the air."""

    #: Point just off the picked surface, on the air side.
    point: Any
    #: Outward normal at the pick, or ``None`` for an edge or vertex.
    normal: Any
    #: The point actually on the surface, before the offset was applied.
    surface_point: Any
    #: ``"Face"``, ``"Edge"`` or ``"Vertex"``.
    kind: str

    @property
    def is_directed(self) -> bool:
        """Whether the pick told us which side the air is on."""
        return self.normal is not None


@dataclass(frozen=True)
class WettedPart:
    """One solid that bounds the cavity, with the wall area it contributes."""

    source: BoundarySolid
    area_mm2: float

    @property
    def label(self) -> str:
        return self.source.label


def _usable(shape: Any) -> bool:
    return shape is not None and hasattr(shape, "isNull") and not shape.isNull()


def _label(obj: Any) -> str:
    return getattr(obj, "Label", None) or getattr(obj, "Name", "object")


def _proxy_name(obj: Any) -> str:
    return type(getattr(obj, "Proxy", None)).__name__


def is_air_object(obj: Any) -> bool:
    """Whether ``obj`` represents air rather than material."""
    return _proxy_name(obj) in AIR_PROXIES


def is_cap_object(obj: Any) -> bool:
    """Whether ``obj`` is a cap, and so exempt from the visibility filter."""
    return _proxy_name(obj) == CAP_PROXY


# ---------------------------------------------------------------------------------
# Turning a pick into a probe point.
# ---------------------------------------------------------------------------------


def _point_on_face(face: Any) -> Any:
    """A point guaranteed to lie on ``face``.

    ``CenterOfMass`` is the obvious candidate and is wrong for anything non-convex,
    holed or curved -- the centroid of a washer is in the hole, and the centroid of a
    cylindrical wall is out on its axis, tens of millimetres off the surface. Projecting
    it back onto the face costs one distance query and always lands on the surface.

    ``Face.isInside`` is not a usable shortcut here even though it looks like one. On a
    face it classifies against the *domain*, not the surface, so it answers true for a
    centroid that is nowhere near the face -- which silently sends a point floating in
    mid-air to the containment tests below, where it matches nothing and the face's area
    is written off as unattributed.
    """
    try:
        return face.distToShape(Part.Vertex(face.CenterOfMass))[1][0][0]
    except Exception as exc:  # noqa: BLE001 -- OCC reports projection failures as text
        raise SeedError(f"could not find a point on the picked face: {exc}") from exc


def _outward_normal(face: Any, point: Any) -> Any:
    """The face normal at ``point``, pointing out of the solid the face belongs to.

    ``Face.normalAt`` already applies the face's orientation, so it *is* the outward
    normal and nothing further is needed. Flipping it again on a ``Reversed`` face --
    which reads plausibly, and which this function did -- points the probe straight into
    the material instead, on roughly half of all faces. The failure is quiet: the probe
    then sits inside a solid, no region contains it, and
    :func:`region_for_probe`'s containment test finds nothing and falls through to
    nearest-region matching, which prefers the *largest* touching region. So a face pick
    beside a small cavity silently returned the exterior and the panel reported a leak
    that was not there -- the exact ambiguity the directed probe exists to remove.

    Verified against FreeCAD 1.1.1 on boxes, cylinders and boolean results: ``normalAt``
    points out of the solid for every face, ``Forward`` and ``Reversed`` alike.
    """
    try:
        u, v = face.Surface.parameter(point)
        return face.normalAt(u, v)
    except Exception as exc:  # noqa: BLE001 -- parameterisation fails on odd surfaces
        raise SeedError(f"could not take a normal on the picked face: {exc}") from exc


def probe_from_subshape(sub: Any, offset: float = PROBE_OFFSET_MM) -> SeedProbe:
    """Reduce a picked sub-shape to a :class:`SeedProbe`.

    A face gives a directed probe: a point offset along the outward normal, which is in
    the air by construction. An edge or a vertex cannot -- an edge is shared by two faces
    whose outward normals disagree, and there is no defensible way to choose between them
    -- so those return the bare point and leave the region match to fall back on distance.
    """
    kind = sub.ShapeType
    if kind == "Face":
        surface_point = _point_on_face(sub)
        normal = _outward_normal(sub, surface_point)
        point = FreeCAD.Vector(
            surface_point.x + normal.x * offset,
            surface_point.y + normal.y * offset,
            surface_point.z + normal.z * offset,
        )
        return SeedProbe(point=point, normal=normal, surface_point=surface_point, kind=kind)

    if kind == "Edge":
        middle = sub.valueAt((sub.FirstParameter + sub.LastParameter) / 2.0)
        return SeedProbe(point=middle, normal=None, surface_point=middle, kind=kind)

    if kind == "Vertex":
        return SeedProbe(point=sub.Point, normal=None, surface_point=sub.Point, kind=kind)

    raise SeedError(
        f"a {kind} cannot seed a cavity. Pick a face on the air side of a part -- an edge "
        f"or a vertex works too, but a face says which side the air is on and so is never "
        f"ambiguous."
    )


def probe_from_reference(obj: Any, subname: str) -> SeedProbe:
    """Resolve a ``(object, subname)`` pick and reduce it to a probe.

    Reuses :func:`~freecad.audio_analysis.capping.resolve_reference`, which already knows
    that a selection inside an assembly carries a topological-naming hash containing dots
    and must not be split at the last one.
    """
    from freecad.audio_analysis.capping import resolve_reference

    _owner, sub = resolve_reference(obj, subname)
    return probe_from_subshape(sub)


# ---------------------------------------------------------------------------------
# Deciding what counts as a boundary part.
# ---------------------------------------------------------------------------------


#: Types that hold other objects instead of geometry of their own. ``App::Part`` covers
#: ``Assembly::AssemblyObject``, and ``App::DocumentObjectGroup`` covers both the Python
#: group the analysis lives in and the assembly's joint group.
CONTAINER_TYPES = ("App::Part", "App::DocumentObjectGroup", "App::LinkGroup")


def _is_container(obj: Any) -> bool:
    """Whether ``obj`` holds other objects rather than geometry of its own.

    Having a ``Group`` is emphatically *not* the test, which cost an afternoon. An
    ``App::Link`` to a PartDesign body republishes that body's feature tree as its own
    ``Group`` -- thirty-four entries of ``Sketch``, ``Pad``, ``Pocket``, ``PolarPattern``
    for one cup. Treating that as a container walks into the *construction history* and
    collects every intermediate solid as if it were a separate part, so a twelve-part
    assembly becomes a hundred-odd overlapping solids and the fuse never returns. A link
    carries its own shape and is always a leaf.
    """
    if hasattr(obj, "isDerivedFrom") and obj.isDerivedFrom("App::Link"):
        return False
    if not getattr(obj, "Group", None):
        return False
    if not hasattr(obj, "isDerivedFrom"):
        return False
    return any(obj.isDerivedFrom(kind) for kind in CONTAINER_TYPES)


def _placed_shape(root: Any, path: str) -> Any | None:
    """The shape at ``path`` below ``root``, in ``root``'s frame.

    ``getSubObject`` applies the accumulated placement, so the result must not be
    transformed again by hand. Plain groups do not transform their children and may not
    implement the call at all, hence the fallback.
    """
    if hasattr(root, "getSubObject"):
        try:
            shape = root.getSubObject(path)
            if _usable(shape):
                return shape
        except Exception:  # noqa: BLE001 -- not every container resolves sub-paths
            pass
    return None


def _solids_of(shape: Any, obj: Any, minimum_volume: float) -> list[BoundarySolid]:
    solids = [s for s in shape.Solids if s.isValid() and s.Volume > minimum_volume]
    label = _label(obj)
    return [
        BoundarySolid(label if len(solids) == 1 else f"{label} (solid {i + 1})", solid)
        for i, solid in enumerate(solids)
    ]


def expand_container(
    root: Any,
    *,
    include_hidden: bool = True,
    minimum_volume: float = 1e-6,
) -> tuple[list[BoundarySolid], list[str]]:
    """Every placed solid inside ``root``, with the object it came from still attached.

    Returns the solids and the labels of anything skipped for being hidden, because a
    part left out of a cavity silently is precisely the failure this module exists to
    remove -- if a body is being ignored, the panel has to be able to say so.

    Expanding rather than taking ``root.Shape`` costs nothing and buys identity: the
    container's own shape is one flat compound in which the parts are anonymous, and
    "solid 7 bounds your cavity" is not an answer anybody can act on.
    """
    found: list[BoundarySolid] = []
    hidden: list[str] = []

    def walk(obj: Any, path: str) -> None:
        for child in getattr(obj, "Group", ()) or ():
            child_path = f"{path}{child.Name}."
            if is_air_object(child):
                continue
            if _is_container(child):
                walk(child, child_path)
                continue
            shape = _placed_shape(root, child_path)
            if shape is None:
                shape = getattr(child, "Shape", None)
            if not _usable(shape) or not shape.Solids:
                continue
            # Caps are exempt: they are made to be hidden once they work.
            if not include_hidden and not getattr(child, "Visibility", True):
                if not is_cap_object(child):
                    hidden.append(_label(child))
                    continue
            found.extend(_solids_of(shape, child, minimum_volume))

    walk(root, "")
    return found, hidden


def _take(
    obj: Any,
    found: list[BoundarySolid],
    hidden: list[str],
    seen: set[int],
    skip: set[int],
    include_hidden: bool,
    minimum_volume: float,
) -> None:
    """Add one object's solids to ``found``, expanding it if it is a container."""
    if id(obj) in skip or id(obj) in seen or is_air_object(obj):
        return
    seen.add(id(obj))
    if _is_container(obj):
        solids, missed = expand_container(
            obj, include_hidden=include_hidden, minimum_volume=minimum_volume
        )
        found.extend(solids)
        hidden.extend(missed)
        return
    shape = getattr(obj, "Shape", None)
    if not _usable(shape) or not shape.Solids:
        return
    if not include_hidden and not getattr(obj, "Visibility", True):
        if not is_cap_object(obj):
            hidden.append(_label(obj))
            return
    found.extend(_solids_of(shape, obj, minimum_volume))


def solids_for(
    objects: Sequence[Any],
    *,
    include_hidden: bool = True,
    minimum_volume: float = 1e-6,
) -> tuple[list[BoundarySolid], list[str]]:
    """Expand an explicit list of boundary objects into placed, labelled solids.

    What :class:`~freecad.audio_analysis.objects.cavity_object.AcousticCavity` uses to
    rebuild the exact solid set the panel previewed. Passing the assembly and letting this
    walk it -- rather than storing the twenty-odd children -- keeps the property list
    readable and lets the cavity follow parts being added to the assembly later.

    Returns the solids and the labels of any body skipped for being hidden.
    """
    found: list[BoundarySolid] = []
    hidden: list[str] = []
    seen: set[int] = set()
    for obj in objects:
        _take(obj, found, hidden, seen, set(), include_hidden, minimum_volume)
    return found, hidden


def collect_candidates(
    doc: Any,
    seed_obj: Any = None,
    *,
    include_hidden: bool = True,
    exclude: Sequence[Any] = (),
    minimum_volume: float = 1e-6,
) -> tuple[list[BoundarySolid], list[str]]:
    """Every solid that could bound the seeded cavity, and what was skipped as hidden.

    Scope follows the pick. A pick inside an assembly means that assembly is the model, so
    a second unrelated assembly in the same document is not dragged in. A pick on a
    standalone body means the document's root solids are the model.

    Caps are gathered from the whole document either way, and regardless of visibility:
    they belong to the analysis rather than to the CAD, so they sit outside whichever
    container the parts live in.
    """
    skip = {id(o) for o in exclude}
    found: list[BoundarySolid] = []
    hidden: list[str] = []
    seen: set[int] = set()

    def take_root(obj: Any) -> None:
        _take(obj, found, hidden, seen, skip, include_hidden, minimum_volume)

    scope = seed_obj if seed_obj is not None and _is_container(seed_obj) else None
    if scope is not None:
        take_root(scope)
    else:
        for obj in doc.RootObjects:
            take_root(obj)

    # Caps live in the analysis group, not in the CAD container, so they need a separate
    # sweep whichever scope was used.
    for obj in doc.Objects:
        if is_cap_object(obj) and id(obj) not in seen and id(obj) not in skip:
            seen.add(id(obj))
            shape = getattr(obj, "Shape", None)
            if _usable(shape) and shape.Solids:
                found.extend(_solids_of(shape, obj, minimum_volume))

    return found, hidden


def source_objects(sources: Iterable[BoundarySolid]) -> list[str]:
    """The distinct labels behind a set of solids, in first-seen order."""
    ordered: list[str] = []
    for source in sources:
        if source.label not in ordered:
            ordered.append(source.label)
    return ordered


# ---------------------------------------------------------------------------------
# Matching the probe to a region, and the region back to the parts.
# ---------------------------------------------------------------------------------


def region_for_probe(
    regions: Sequence[CavityRegion], probe: SeedProbe
) -> CavityRegion | None:
    """The void region the probe sits in, or ``None`` if the pick touches no air.

    A directed probe -- one from a face -- is tested by containment, which is exact. An
    edge or vertex probe lies *on* the boundary and so is contained by nothing, and falls
    back to the nearest region, which is the best available reading of an ambiguous pick.
    """
    if probe.is_directed:
        for region in regions:
            if region.shape.isInside(probe.point, 1e-7, True):
                return region

    vertex = Part.Vertex(probe.surface_point)
    nearest: CavityRegion | None = None
    best = float("inf")
    for region in regions:
        try:
            distance = region.shape.distToShape(vertex)[0]
        except Exception:  # noqa: BLE001 -- a degenerate region is simply not a match
            continue
        # Prefer a genuine touch; among touching regions prefer the largest, since a pick
        # on a rim can legitimately border both a cavity and the screw hole beside it.
        if distance < ON_SURFACE_TOLERANCE_MM:
            if nearest is None or best >= ON_SURFACE_TOLERANCE_MM or (
                region.volume_mm3 > nearest.volume_mm3
            ):
                nearest, best = region, min(best, distance)
        elif nearest is None and distance < best:
            nearest, best = region, distance
    return nearest


def grown_box(solid: Any) -> Any:
    """``solid``'s bounding box, widened by the on-surface tolerance.

    The widening is the whole point. Every point tested against this box lies *on* the
    boundary between a part and the void, which means it lands on that part's bounding box
    too -- a flat cap's entire wetted face sits at its own box's limit. ``BoundBox.isInside``
    tolerates a point exactly on the boundary but rejects one a nanometre past it, and a
    surface point computed through a boolean lands on either side at random. Ungrown, the
    box therefore rejects a scattering of precisely the points the test exists to accept,
    and the part disappears from the wall list of the cavity it bounds.
    """
    box = FreeCAD.BoundBox(solid.BoundBox)
    box.enlarge(ON_SURFACE_TOLERANCE_MM)
    return box


def lies_on(solid: Any, box: Any, point: Any) -> bool:
    """Whether ``point`` lies on ``solid``'s surface. ``box`` comes from :func:`grown_box`.

    The box is a cheap rejection ahead of the solid classifier, which is the expensive
    call and the one that decides.
    """
    if not box.isInside(point):
        return False
    return solid.isInside(point, ON_SURFACE_TOLERANCE_MM, True)


def wetted_parts(
    region: CavityRegion, sources: Sequence[BoundarySolid]
) -> tuple[list[WettedPart], float]:
    """Which solids bound ``region``, and how much wall area each contributes.

    Returns the parts sorted by descending area, plus the area that could not be
    attributed to any of them. For an enclosed cavity that remainder should be zero: every
    wall belongs to some part. A non-zero remainder on an enclosed region means a face of
    the void came from the envelope, which should be impossible, so it is surfaced rather
    than swallowed.

    Attribution is by sampling rather than by solid-to-solid distance. Both give the same
    answer, but ``distToShape`` between two detailed solids compares every face against
    every face; asking instead whether one point lies on one solid's surface is a
    classifier query, and doing it once per wall face is measurably cheaper on a real
    assembly. Sampling also yields the *area* each part carries, which the distance test
    cannot, and that is the number that tells a structural screw apart from a wall.
    """
    box = FreeCAD.BoundBox(region.shape.BoundBox)
    box.enlarge(ON_SURFACE_TOLERANCE_MM)
    candidates = [s for s in sources if box.intersect(s.solid.BoundBox)]

    boxes = [grown_box(s.solid) for s in candidates]

    areas: dict[int, float] = {}
    unattributed = 0.0

    for face in region.shape.Faces:
        try:
            point = _point_on_face(face)
        except SeedError:
            unattributed += face.Area
            continue
        for index, source in enumerate(candidates):
            if lies_on(source.solid, boxes[index], point):
                areas[index] = areas.get(index, 0.0) + face.Area
                break
        else:
            unattributed += face.Area

    parts = [
        WettedPart(candidates[index], area)
        for index, area in sorted(areas.items(), key=lambda kv: -kv[1])
    ]
    return parts, unattributed


def describe_wetted(parts: Sequence[WettedPart], unattributed: float = 0.0) -> str:
    """A listing of the bounding parts by share of wall area."""
    if not parts:
        return "no bounding parts identified"
    total = sum(p.area_mm2 for p in parts) + unattributed
    if total <= 0.0:
        return "no bounding parts identified"
    lines = [
        f"{p.label}: {p.area_mm2:.0f} mm2 ({100.0 * p.area_mm2 / total:.1f}%)"
        for p in parts
    ]
    if unattributed > 0.0:
        lines.append(
            f"unattributed: {unattributed:.0f} mm2 "
            f"({100.0 * unattributed / total:.1f}%) -- wall that belongs to no part"
        )
    return "\n".join(lines)
