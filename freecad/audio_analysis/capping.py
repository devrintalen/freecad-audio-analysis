"""Turning an opening in a CAD part into a cap solid.

Cavity extraction (:mod:`freecad.audio_analysis.cavity`) can only find air that is
*enclosed*, and a real headphone cup is not: it is open where the ear goes, and open again
at every intentional port. So the user has to supply a cap — a solid that plugs the
opening — before there is any volume to measure.

Modelling those by hand is tedious and easy to get subtly wrong, which is what this module
removes. Point at **one edge of the opening** and it recovers the rest of the loop, the
same way PartDesign's fillet expands from a single edge to the ones that continue from it.

**Capping is not sealing.** This is the point most likely to be misread, so it is worth
stating plainly: a cap closes the *fluid domain* so the boolean has something bounded to
find. It does not assert that the opening is acoustically closed. A port that has been
capped here reappears in the lumped network as a ``Port`` — with, if it is covered, an
``AcousticResistance`` in series — and :attr:`Opening.area_mm2` is exactly the number that
element needs. Cap every opening, then decide in the *network* which ones are open.

**How the loop is found.** An opening in a solid is a *hole in a face*, and a hole in a
face is an **inner wire** — one of the face's wires that is not its outer boundary. That
distinction is what makes the search reliable, and picking the shortest closed loop instead
is not good enough: a slot bored through a wall has a rim of perimeter 2(l+w), while the
side wall of its own bore is a closed loop of perimeter 2(w+t), which for any slot longer
than the wall is thick is *shorter*. Capping that would plug the side of the bore rather
than its mouth. So candidates containing the picked edge are gathered, those that are an
inner wire of some face are preferred, and only within that group does the shortest win.
Picking a different edge picks a different loop, which is what makes a single click enough.

**A contoured rim is capped flat, on purpose.** An earpad's ear-side opening is a closed
loop that does not lie in a plane — it waves a few millimetres as it follows the pad. The
cap for it is a flat disc on the loop's best-fit plane, not a surface that follows the
contour. That is not a simplification made for convenience: the plane *is* the ear plane,
and representing the ear as a flat boundary carrying an acoustic impedance is what an
artificial ear physically is (§6.4, route C). The extrusion is then lengthened by the rim's
out-of-plane deviation at both ends, so the disc still crosses the material everywhere
around its circumference.

**Why the cap is grown slightly.** A cap built on the loop exactly meets the surrounding
material along a curve rather than overlapping it, and two solids that touch along a curve
are precisely the input OpenCascade booleans handle worst — the union may succeed and still
leave a path through the contact. Growing the outline by :data:`DEFAULT_OVERLAP_MM` in the
plane of the opening, and straddling that plane by the cap's thickness, gives a genuine
volumetric overlap instead. The reported area is always measured on the *original* loop, so
the overlap never inflates the port area it feeds.

**The growth is a scale, not an offset, and that is not a shortcut.** The obvious way to
grow an outline is ``Wire.makeOffset2D``, and it cannot be used here.
``BRepOffsetAPI_MakeOffset`` rejects many entirely ordinary port outlines — an 0.8 mm bored
circle in the model this was developed against, among others — and it does not fail
cleanly: each raised ``CADKernelError`` leaves the kernel slightly worse, and a run of them
**segfaults the process**, taking the user's unsaved document with it. Measured, not
theorised: seventeen openings offset in sequence, each individually recoverable, killed
FreeCAD on the seventeenth. So the outline is enlarged by scaling it about its own centroid
instead (:func:`grow_face`), which is a plain affine transform that cannot fail.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import FreeCAD
import Part

#: Cap thickness in mm. Straddles the opening plane, half on each side.
DEFAULT_THICKNESS_MM = 2.0

#: How far the cap outline is grown beyond the opening, in mm, so it overlaps the
#: surrounding material rather than merely touching it.
DEFAULT_OVERLAP_MM = 0.5

#: Tolerance for deciding two edges are the same one, in mm. Generous, because the
#: comparison is between copies of a shape returned by different resolution paths.
SAME_EDGE_TOLERANCE_MM = 1e-6

#: Target spacing, in mm, when sampling a contoured rim to flatten it. The chord error of
#: a 1 mm step on a 30 mm radius is under a micron, so this costs nothing in accuracy.
FLATTEN_SAMPLE_SPACING_MM = 1.0

#: Never sample a rim into more than this many points; a cap does not need thousands of
#: side faces, and every one of them is work for the boolean that follows.
MAX_FLATTEN_SAMPLES = 720

#: Warn when a rim departs from its best-fit plane by more than this fraction of its
#: equivalent radius. Below it, flattening is a detail; above it, the cap is a real
#: modelling choice the user should be told about.
FLATNESS_WARNING_FRACTION = 0.1

#: How nearly collinear two edges must be, as ``cos(angle)``, to count as continuing one
#: another when walking a rim by tangent. 0.9 is about 26 degrees -- loose enough to follow
#: a polygonal approximation of a curve, tight enough that a seam line meeting a bore mouth
#: at a right angle is never mistaken for its continuation.
TANGENT_CONTINUITY = 0.9

#: Tolerance for deciding two vertices are the same point, in mm.
VERTEX_TOLERANCE_MM = 1e-4


class CapError(ValueError):
    """Raised when an opening cannot be resolved or turned into a cap."""


@dataclass(frozen=True)
class Opening:
    """One closed loop that bounds an opening, and what a cap for it would cover."""

    #: Human-readable source, e.g. ``"Cup.Edge148"``.
    label: str
    #: The closed wire bounding the opening.
    wire: Any
    #: Names of the loop's edges within the owning shape, for the report.
    edge_names: tuple[str, ...]
    #: Area of the opening itself, before any overlap is added. For a contoured rim this
    #: is the area of its projection onto the best-fit plane -- the aperture, not the
    #: developed surface.
    area_mm2: float
    #: False when the loop does not lie in a plane and had to be flattened.
    planar: bool
    #: How far the loop departs from its best-fit plane, in mm, either side.
    flatness_mm: float = 0.0

    @property
    def area_cm2(self) -> float:
        return self.area_mm2 / 100.0

    @property
    def equivalent_radius_mm(self) -> float:
        """Radius of a circle of the same area -- the scale the flatness is judged against."""
        return math.sqrt(self.area_mm2 / math.pi) if self.area_mm2 > 0.0 else 0.0

    @property
    def badly_out_of_plane(self) -> bool:
        """True when the rim is warped enough that a flat cap is a real approximation."""
        radius = self.equivalent_radius_mm
        return radius > 0.0 and self.flatness_mm > FLATNESS_WARNING_FRACTION * radius

    def describe(self) -> str:
        edges = ", ".join(self.edge_names) if self.edge_names else "unnamed"
        if self.planar:
            flatness = "planar"
        else:
            flatness = (
                f"contoured, +/-{self.flatness_mm:.2f} mm out of plane, "
                f"capped flat on its best-fit plane"
            )
        return (
            f"{self.label}: {len(self.wire.Edges)} edge(s), {flatness}, "
            f"area {self.area_mm2:.1f} mm2 ({self.area_cm2:.3f} cm2), "
            f"perimeter {self.wire.Length:.1f} mm\n"
            f"    edges: {edges}"
        )


# ---------------------------------------------------------------------------------
# Resolving a picked sub-element, including through an assembly
# ---------------------------------------------------------------------------------


def _label(obj: Any) -> str:
    return getattr(obj, "Label", None) or getattr(obj, "Name", "object")


def split_subname(obj: Any, subname: str) -> tuple[str, str]:
    """Split ``subname`` into ``(object_path, element_name)``.

    This is the trap that made a valid pick look like a broken one. A GUI selection inside
    an assembly does not look like ``"Body004.PolarPattern001.Edge148"`` but like ::

        Body004.PolarPattern001.;#2460:f;:G2#2bdc;CUT;:H-87b:d,E;:H87b,E.Edge148

    The middle segment is FreeCAD's topological-naming element map, and **it contains
    dots**. Cutting at the last dot therefore leaves part of that hash in the object path,
    which then resolves to the picked edge alone rather than to the part -- no faces, no
    wires, and a report that a perfectly good rim edge belongs to no closed loop.

    ``resolveSubElement`` knows where the boundary really is, so it does the splitting and
    the hand-rolled version is only the fallback for objects that lack it.

    A ``"?Edge148"`` coming back means the mapped name could not be found -- the feature
    was rebuilt since the pick -- and FreeCAD is offering the plain index as its best
    guess. That guess is taken: a stale topological name is a reason to re-pick the edge,
    not a reason to refuse geometry that is very probably still right.
    """
    if hasattr(obj, "resolveSubElement"):
        try:
            _, mapped, element = obj.resolveSubElement(subname, False)
            element = (element or "").lstrip("?")
            if mapped and element and subname.endswith(mapped):
                return subname[: len(subname) - len(mapped)], element
        except Exception:  # noqa: BLE001 -- fall through to the naive split
            pass

    element = subname.rsplit(".", 1)[-1]
    return subname[: len(subname) - len(element)], element


def reference_label(obj: Any, subname: str) -> str:
    """``"Assembly.Body004.PolarPattern001.Edge148"`` -- the pick, minus the element map.

    The raw subname carries a topological-naming hash that is meaningless to read and long
    enough to bury the rest of the line. What the user needs from a report is which part
    and which edge.
    """
    path, element = split_subname(obj, subname)
    return f"{_label(obj)}.{path}{element}"


def placed_owner_shape(obj: Any, path: str) -> Any | None:
    """The shape of the object at ``path``, transformed into the frame it is seen in.

    ``getSubObject`` applies the accumulated placement itself, so the shape comes back
    already assembled -- which is why the transform must *not* be applied a second time by
    hand. An empty path means ``obj`` itself.
    """
    if not path:
        shape = getattr(obj, "Shape", None)
        return shape if _usable(shape) else None
    if not hasattr(obj, "getSubObject"):
        return None
    try:
        shape = obj.getSubObject(path)
    except Exception:  # noqa: BLE001 -- not every object supports sub-object paths
        return None
    return shape if _usable(shape) else None


def resolve_reference(obj: Any, subname: str) -> tuple[Any, Any]:
    """Resolve ``subname`` to ``(owner_shape, sub_shape)``, both in the same frame.

    ``subname`` may be a plain element name (``"Edge148"``), a path through an assembly, or
    that path with an element-map hash embedded in it — see :func:`split_subname`.

    The element is always looked up *within the owner shape* rather than resolved by a
    second, independent call. That is what keeps the two halves in one coordinate frame:
    resolved separately they can come back as an owner in the part's frame and an edge in
    the assembly's, and the loop search then silently matches nothing instead of failing
    (STRUCTURE.md §6.5 on placements).
    """
    if not subname:
        raise CapError(f"{_label(obj)}: no sub-element named in the reference")

    path, element = split_subname(obj, subname)

    owner = placed_owner_shape(obj, path)
    if owner is not None:
        try:
            return owner, owner.getElement(element)
        except Exception:  # noqa: BLE001 -- fall through to the flat lookup
            pass

    shape = getattr(obj, "Shape", None)
    if not _usable(shape):
        raise CapError(f"{_label(obj)} has no shape to pick from")
    try:
        return shape, shape.getElement(element)
    except Exception as exc:  # noqa: BLE001 -- stale reference after a rebuild
        raise CapError(
            f"{_label(obj)}.{subname} could not be resolved; the geometry may have "
            f"changed since it was picked"
        ) from exc


def _usable(shape: Any) -> bool:
    return shape is not None and hasattr(shape, "isNull") and not shape.isNull()


def _same_edge(a: Any, b: Any) -> bool:
    """Whether two edges are geometrically the same one.

    Compared by geometry rather than by ``isSame``, because the picked edge and the edges
    of the owning shape may be different copies -- ``getSubObject`` does not promise
    identity -- and ``isSame`` on copies is False even when they coincide exactly.
    """
    if abs(a.Length - b.Length) > SAME_EDGE_TOLERANCE_MM:
        return False
    return a.CenterOfMass.distanceToPoint(b.CenterOfMass) <= SAME_EDGE_TOLERANCE_MM


def edge_names_in(shape: Any, wire: Any) -> tuple[str, ...]:
    """``("Edge148", "Edge152", ...)`` for the wire's edges, as named by ``shape``."""
    names: list[str] = []
    for index, candidate in enumerate(shape.Edges, start=1):
        if any(_same_edge(candidate, edge) for edge in wire.Edges):
            names.append(f"Edge{index}")
    return tuple(names)


# ---------------------------------------------------------------------------------
# Finding the loop
# ---------------------------------------------------------------------------------


def loop_candidates(shape: Any, edge: Any) -> list[tuple[Any, bool]]:
    """``(wire, is_hole)`` for every distinct closed face wire of ``shape`` through ``edge``.

    Ordered by whether the wire is a hole in some face before length, for the reason given
    in the module docstring: the mouth of an opening is an inner wire, and the shortest
    loop through the same edge is often the side of the bore instead.

    A wire counts as a hole if it is an inner wire of *any* face it belongs to. It can be
    the outer boundary of another face at the same time -- a bore's mouth usually is -- and
    that does not make it less of an opening.
    """
    candidates: list[list[Any]] = []  # [wire, is_hole]
    for face in shape.Faces:
        try:
            boundary = face.OuterWire
        except Exception:  # noqa: BLE001 -- a degenerate face has no outer wire
            boundary = None
        for wire in face.Wires:
            if not wire.isClosed():
                continue
            if not any(_same_edge(candidate, edge) for candidate in wire.Edges):
                continue
            is_hole = boundary is None or not _same_wire(wire, boundary)
            for existing in candidates:
                if _same_wire(existing[0], wire):
                    existing[1] = existing[1] or is_hole
                    break
            else:
                candidates.append([wire, is_hole])

    candidates.sort(key=lambda entry: (not entry[1], entry[0].Length))
    return [(wire, is_hole) for wire, is_hole in candidates]


def closed_loops_containing(shape: Any, edge: Any) -> list[Any]:
    """Every distinct closed wire of ``shape`` containing ``edge``, best candidate first."""
    return [wire for wire, _ in loop_candidates(shape, edge)]


def hole_loops_containing(shape: Any, edge: Any) -> list[Any]:
    """Only the candidates that are a hole in some face, shortest first."""
    return [wire for wire, is_hole in loop_candidates(shape, edge) if is_hole]


def _tangent_away(edge: Any, point: Any) -> Any | None:
    """Unit tangent of ``edge`` at whichever end sits on ``point``, pointing away from it."""
    for parameter, sign in ((edge.FirstParameter, 1.0), (edge.LastParameter, -1.0)):
        try:
            here = edge.valueAt(parameter)
        except Exception:  # noqa: BLE001 -- degenerate parameterisation
            return None
        if here.distanceToPoint(point) <= VERTEX_TOLERANCE_MM:
            try:
                tangent = edge.tangentAt(parameter)
            except Exception:  # noqa: BLE001
                return None
            if tangent.Length == 0.0:
                return None
            return tangent.normalize() * sign
    return None


def tangent_loop(shape: Any, edge: Any) -> Any | None:
    """Walk outwards from ``edge`` along tangent-continuous neighbours until it closes.

    This is the rule PartDesign's fillet uses to expand a selection, and it is needed for a
    topology the face-wire search cannot see. When a bore pierces a **periodic** surface --
    a cylinder, a sphere -- OpenCascade often does not represent the resulting hole as an
    inner wire at all. It joins the hole to the face's own boundary along the seam, leaving
    one wire that runs round the outside, up the seam, around the bore and back. The mouth
    then exists only as a *sub-chain* of that wire, so there is nothing for
    :func:`loop_candidates` to return, and the shortest closed wire through the picked edge
    is the side wall of the bore.

    Tangent continuity separates them cleanly: the two arcs of a bore mouth continue one
    another smoothly, while the seam line meets them at a right angle.

    Returns None when the walk does not close, or when no continuation is smooth enough --
    guessing a branch is how a cap ends up spanning two openings at once.
    """
    if edge.isClosed():
        return Part.Wire([edge])
    vertices = [v.Point for v in edge.Vertexes]
    if len(vertices) < 2:
        return None

    start, end = vertices[0], vertices[-1]
    chain = [edge]

    for _ in range(len(shape.Edges)):
        outgoing = _tangent_away(chain[-1], end)
        if outgoing is None:
            return None

        best, best_score = None, TANGENT_CONTINUITY
        for candidate in shape.Edges:
            if any(_same_edge(candidate, taken) for taken in chain):
                continue
            incoming = _tangent_away(candidate, end)
            if incoming is None:
                continue
            # Both point away from the shared vertex, so a smooth continuation is the
            # pair that most nearly opposes.
            score = -outgoing.dot(incoming)
            if score > best_score:
                best, best_score = candidate, score
        if best is None:
            return None

        chain.append(best)
        ends = [v.Point for v in best.Vertexes]
        end = (
            ends[0]
            if ends[-1].distanceToPoint(end) <= VERTEX_TOLERANCE_MM
            else ends[-1]
        )
        if end.distanceToPoint(start) <= VERTEX_TOLERANCE_MM:
            try:
                wire = Part.Wire(Part.sortEdges(chain)[0])
            except Exception:  # noqa: BLE001 -- an unsortable chain is not a loop
                return None
            return wire if wire.isClosed() else None
    return None


def _same_wire(a: Any, b: Any) -> bool:
    if len(a.Edges) != len(b.Edges) or abs(a.Length - b.Length) > SAME_EDGE_TOLERANCE_MM:
        return False
    return all(any(_same_edge(x, y) for y in b.Edges) for x in a.Edges)


def loop_for_edge(shape: Any, edge: Any) -> Any:
    """The loop of ``shape`` that best represents the opening through ``edge``.

    Three routes, in order of how much they can be trusted:

    1. **A hole in a face.** The mouth of an opening, when OpenCascade represents it as
       one. Covers a bored hole, a slot, and a pad's inner rim.
    2. **A tangent-continuous walk.** For a bore through a periodic surface, where the
       hole is seam-connected to the face boundary and no inner wire exists at all. See
       :func:`tangent_loop`.
    3. **The shortest closed face wire**, then an unambiguous walk. Last resorts, and both
       can be wrong in ways the user has to look at the result to notice.
    """
    holes = hole_loops_containing(shape, edge)
    if holes:
        return holes[0]

    tangential = tangent_loop(shape, edge)
    if tangential is not None:
        return tangential

    loops = closed_loops_containing(shape, edge)
    if loops:
        return loops[0]

    chain = _walk_from(shape, edge)
    if chain is not None:
        return chain
    raise CapError(
        "the picked edge is not part of any closed loop, so there is no opening to "
        "cap. Pick an edge that runs around the rim of the hole; an edge across a face, "
        "or one on an open surface, has no loop to expand into."
    )


def _walk_from(shape: Any, edge: Any) -> Any | None:
    """Grow a closed wire outwards from ``edge`` along connected edges.

    Only used when the face-wire search fails. Stops as soon as the chain closes, and
    gives up where the continuation is ambiguous rather than guessing a branch.
    """
    chain = [edge]
    for _ in range(len(shape.Edges)):
        try:
            wire = Part.Wire(Part.sortEdges(chain)[0])
        except Exception:  # noqa: BLE001 -- an unsortable chain is not a loop
            return None
        if wire.isClosed():
            return wire
        ends = [wire.Vertexes[0].Point, wire.Vertexes[-1].Point]
        candidates = [
            other
            for other in shape.Edges
            if not any(_same_edge(other, taken) for taken in chain)
            and any(
                any(v.Point.distanceToPoint(end) <= SAME_EDGE_TOLERANCE_MM for end in ends)
                for v in other.Vertexes
            )
        ]
        if len(candidates) != 1:
            return None
        chain.append(candidates[0])
    return None


def wires_from_edges(edges: Sequence[Any]) -> list[Any]:
    """Closed wires formed by the given edges alone, if they happen to make any.

    Lets an explicit multi-edge selection win over propagation: if the user picked the
    whole loop themselves, that is a statement of intent and guessing again would only be
    a chance to get it wrong.
    """
    if len(edges) < 2:
        return []
    try:
        chains = Part.sortEdges(list(edges))
    except Exception:  # noqa: BLE001 -- unrelated edges do not sort into chains
        return []

    closed: list[Any] = []
    for chain in chains:
        try:
            wire = Part.Wire(chain)
        except Exception:  # noqa: BLE001
            continue
        if wire.isClosed():
            closed.append(wire)
    return closed


# ---------------------------------------------------------------------------------
# Building the cap
# ---------------------------------------------------------------------------------


def best_fit_plane(points: Any) -> tuple[Any, Any]:
    """``(centroid, normal)`` of the plane that best fits ``points``, by SVD.

    The normal is the direction of least variance, which for a rim that waves about a
    plane is the plane's own normal.
    """
    import numpy as np

    array = np.asarray([[p.x, p.y, p.z] for p in points], dtype=float)
    centroid = array.mean(axis=0)
    _, _, right = np.linalg.svd(array - centroid, full_matrices=False)
    normal = right[2]
    return (
        FreeCAD.Vector(*centroid),
        FreeCAD.Vector(*normal).normalize(),
    )


def flattened_face(wire: Any) -> tuple[Any, float]:
    """``wire`` projected onto its best-fit plane as a face, with how far it had to move.

    An earpad's ear-side rim, or any opening around a contoured surface, is a closed loop
    that does not lie in a plane. The right cap for it is **flat**: that plane is the ear
    plane, and representing the ear as a flat boundary carrying an impedance is what an
    artificial ear physically is (§6.4, route C). A cap that followed the contour would be
    harder to build, no more correct, and would still have to be flat wherever a probe or
    an impedance termination attached to it.

    The alternative tried first was ``Part.makeFilledFace``. On a real earpad it fitted a
    warped surface of a third the aperture's area, and extruding that along a single normal
    produced a 74 mm-deep flange instead of a disc. Flattening is not a fallback here; it
    is the correct construction.
    """
    samples = min(
        MAX_FLATTEN_SAMPLES,
        max(64, int(wire.Length / FLATTEN_SAMPLE_SPACING_MM)),
    )
    try:
        points = wire.discretize(Number=samples)
    except Exception as exc:  # noqa: BLE001 -- a degenerate wire cannot be sampled
        raise CapError(
            "could not sample this loop to flatten it, so no cap can be built across it."
        ) from exc
    if len(points) < 3:
        raise CapError("this loop has too few distinct points to span with a cap")

    centroid, normal = best_fit_plane(points)

    projected: list[Any] = []
    deviation = 0.0
    for point in points:
        offset = (point - centroid).dot(normal)
        deviation = max(deviation, abs(offset))
        projected.append(point - normal * offset)

    # Close the polygon explicitly; discretize returns the start point once, not twice.
    if projected[0].distanceToPoint(projected[-1]) > SAME_EDGE_TOLERANCE_MM:
        projected.append(projected[0])

    try:
        face = Part.Face(Part.makePolygon(projected))
    except Exception as exc:  # noqa: BLE001
        raise CapError(
            "the loop does not project to a simple outline -- it crosses itself when "
            "flattened, which usually means it wanders over more than one opening. Pick "
            "an edge on a single rim."
        ) from exc
    return face, deviation


def face_from_wire(wire: Any) -> tuple[Any, bool, float]:
    """``(face, planar, deviation_mm)`` -- a face spanning ``wire``.

    A planar loop gives an exact face and zero deviation. Anything else is flattened onto
    its best-fit plane by :func:`flattened_face`.
    """
    try:
        return Part.Face(wire), True, 0.0
    except Exception:  # noqa: BLE001 -- non-planar wires cannot make a planar face
        pass
    face, deviation = flattened_face(wire)
    return face, False, deviation


def _normal_of(face: Any) -> Any:
    """Outward normal at the middle of ``face``'s parameter range."""
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)


def grow_face(face: Any, distance: float) -> Any:
    """``face`` enlarged by roughly ``distance`` all round, scaled about its own centroid.

    A scale rather than a true 2D offset, for the reason set out in the module docstring:
    ``makeOffset2D`` can take the whole process down, and no amount of exception handling
    catches a segfault. Scaling is a plain affine transform with no kernel algorithm behind
    it. For a planar face the centroid lies in the face's own plane, so scaling about it
    moves nothing out of that plane -- the growth is purely in-plane, which is what the
    overlap needs to be.

    The factor is derived from the equivalent radius, so a circle grows by exactly
    ``distance``. Other outlines grow proportionally instead of uniformly: a long slot
    gains a little more along its length than across it. The purpose is only to overlap the
    surrounding material rather than to meet it exactly, so that difference has no
    consequence -- and the opening area reported to the user is measured on the original
    loop regardless.
    """
    if distance <= 0.0 or face.Area <= 0.0:
        return face

    factor = 1.0 + distance / math.sqrt(face.Area / math.pi)

    # Shape.scale, not transformGeometry: a uniform scale is a native OCC transform and
    # leaves a circle a circle, where transformGeometry re-approximates every curve as a
    # spline and loses a measurable fraction of the area doing it.
    grown = face.copy()
    grown.scale(factor, face.CenterOfMass)
    faces = grown.Faces
    return faces[0] if faces else face


def cap_solid(
    wire: Any,
    thickness: float = DEFAULT_THICKNESS_MM,
    overlap: float = DEFAULT_OVERLAP_MM,
) -> Any:
    """A solid plug spanning ``wire``, straddling its plane.

    ``thickness`` is the depth of material either side of the opening, so the cap always
    crosses the surface it plugs regardless of which way round the part faces.

    A contoured rim is capped flat on its best-fit plane, and then the extrusion is grown
    by the rim's out-of-plane deviation at both ends. Without that a 2 mm cap across a rim
    that waves +/-2 mm would stand clear of the material over part of its circumference and
    seal nothing -- the failure would show up much later, as an extraction that still finds
    the model open.
    """
    if thickness <= 0.0:
        raise CapError(f"cap thickness must be positive, got {thickness} mm")

    face, planar, deviation = face_from_wire(wire)
    face = grow_face(face, overlap)

    span = thickness + 2.0 * deviation
    normal = _normal_of(face)
    solid = face.extrude(normal * span)
    solid.translate(normal * (-span / 2.0))

    if not solid.Solids:
        raise CapError("extruding the cap outline produced no solid")
    return solid


def opening_from_wire(wire: Any, label: str, owner: Any = None) -> Opening:
    """Measure a loop: its open area, its flatness, and the edges it is made of."""
    face, planar, deviation = face_from_wire(wire)
    names = edge_names_in(owner, wire) if owner is not None else ()
    return Opening(
        label=label,
        wire=wire,
        edge_names=names,
        area_mm2=face.Area,
        planar=planar,
        flatness_mm=deviation,
    )


def openings_from_references(references: Any, propagate: bool = True) -> list[Opening]:
    """Resolve an ``App::PropertyLinkSubList`` to the openings it identifies.

    Faces contribute their outer wire. Edges contribute either the loop they already form
    together, or -- with ``propagate`` -- the tightest loop each one belongs to.
    """
    resolved: list[tuple[str, Any, Any]] = []  # (label, owner_shape, sub_shape)
    for entry in references or []:
        try:
            obj, names = entry
        except (TypeError, ValueError) as exc:
            raise CapError(f"malformed geometry reference: {entry!r}") from exc
        for name in [names] if isinstance(names, str) else (names or ()):
            owner, sub = resolve_reference(obj, name)
            resolved.append((reference_label(obj, name), owner, sub))

    if not resolved:
        raise CapError(
            "no edges or faces referenced. Pick one edge on the rim of the opening -- "
            "the rest of the loop is found from it."
        )

    openings: list[Opening] = []

    faces = [(lbl, owner, s) for lbl, owner, s in resolved if isinstance(s, Part.Face)]
    for label, owner, face in faces:
        openings.append(opening_from_wire(face.OuterWire, label, owner))

    edges = [(lbl, owner, s) for lbl, owner, s in resolved if isinstance(s, Part.Edge)]
    if edges:
        explicit = wires_from_edges([edge for _, _, edge in edges])
        if explicit:
            owner = edges[0][1]
            joined = ", ".join(label for label, _, _ in edges)
            for wire in explicit:
                openings.append(opening_from_wire(wire, joined, owner))
        elif propagate:
            for label, owner, edge in edges:
                openings.append(opening_from_wire(loop_for_edge(owner, edge), label, owner))
        else:
            raise CapError(
                "the selected edges do not form a closed loop on their own, and "
                "Propagate is off. Turn it on to expand from a single edge, or select "
                "every edge of the loop."
            )

    if not openings:
        raise CapError(
            "the reference resolved to neither a face nor an edge. Cap works from the "
            "rim of an opening: pick an edge on it, or the face that spans it."
        )
    return _deduplicate(openings)


def _deduplicate(openings: Sequence[Opening]) -> list[Opening]:
    """Drop loops picked more than once -- two edges of the same rim give one cap."""
    unique: list[Opening] = []
    for opening in openings:
        if not any(_same_wire(opening.wire, seen.wire) for seen in unique):
            unique.append(opening)
    return unique


def build_caps(
    references: Any,
    thickness: float = DEFAULT_THICKNESS_MM,
    overlap: float = DEFAULT_OVERLAP_MM,
    propagate: bool = True,
) -> tuple[Any, list[Opening]]:
    """The cap shape for every referenced opening, plus what was found.

    Several openings give one compound, so a polar pattern of ports can be capped by
    picking one edge on each and still arrive as a single object.
    """
    openings = openings_from_references(references, propagate)
    solids = [cap_solid(o.wire, thickness, overlap) for o in openings]
    shape = solids[0] if len(solids) == 1 else Part.makeCompound(solids)
    return shape, openings


def describe_openings(openings: Sequence[Opening]) -> str:
    """A listing for the report view, led by the total open area."""
    if not openings:
        return "no openings found"
    total = sum(o.area_mm2 for o in openings)
    lines = [
        f"{len(openings)} opening(s), {total:.1f} mm2 ({total / 100.0:.3f} cm2) total. "
        f"That total is the open area -- give it to a Port if this opening is meant to "
        f"stay acoustically open."
    ]
    lines.extend(o.describe() for o in openings)
    return "\n".join(lines)
