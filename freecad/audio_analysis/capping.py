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

import Part

#: Cap thickness in mm. Straddles the opening plane, half on each side.
DEFAULT_THICKNESS_MM = 2.0

#: How far the cap outline is grown beyond the opening, in mm, so it overlaps the
#: surrounding material rather than merely touching it.
DEFAULT_OVERLAP_MM = 0.5

#: Tolerance for deciding two edges are the same one, in mm. Generous, because the
#: comparison is between copies of a shape returned by different resolution paths.
SAME_EDGE_TOLERANCE_MM = 1e-6


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
    #: Area of the opening itself, before any overlap is added.
    area_mm2: float
    #: False when the loop does not lie in a plane, so the cap is a filled surface.
    planar: bool

    @property
    def area_cm2(self) -> float:
        return self.area_mm2 / 100.0

    def describe(self) -> str:
        edges = ", ".join(self.edge_names) if self.edge_names else "unnamed"
        flatness = "planar" if self.planar else "NON-PLANAR (filled surface)"
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


def resolve_reference(obj: Any, subname: str) -> tuple[Any, Any]:
    """Resolve ``subname`` to ``(owner_shape, sub_shape)``, both in the same frame.

    ``subname`` may be a plain element name (``"Edge148"``) or a path through an assembly
    (``"Body004.PolarPattern001.Edge148"``), which is what selecting inside an assembly
    actually produces. The dotted form cannot be resolved by ``Shape.getElement`` — the
    assembly's own shape has no edge by that name — so ``getSubObject`` is tried first and
    the flat lookup is the fallback.

    Both halves come from the same strategy deliberately. Mixing them would return an owner
    in one coordinate frame and an edge in another, and the loop search would then silently
    match nothing (STRUCTURE.md §6.5 on placements).
    """
    if not subname:
        raise CapError(f"{_label(obj)}: no sub-element named in the reference")

    element = subname.rsplit(".", 1)[-1]
    prefix = subname[: len(subname) - len(element)]

    if hasattr(obj, "getSubObject"):
        try:
            sub = obj.getSubObject(subname)
            owner = obj.getSubObject(prefix) if prefix else getattr(obj, "Shape", None)
            if _usable(sub) and _usable(owner):
                return owner, sub
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


def closed_loops_containing(shape: Any, edge: Any) -> list[Any]:
    """Every distinct closed wire of ``shape`` containing ``edge``, best candidate first.

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
    return [wire for wire, _ in candidates]


def _same_wire(a: Any, b: Any) -> bool:
    if len(a.Edges) != len(b.Edges) or abs(a.Length - b.Length) > SAME_EDGE_TOLERANCE_MM:
        return False
    return all(any(_same_edge(x, y) for y in b.Edges) for x in a.Edges)


def loop_for_edge(shape: Any, edge: Any) -> Any:
    """The loop of ``shape`` that best represents the opening through ``edge``.

    Falls back to walking the edge's connected neighbours when the edge belongs to no
    closed face wire, which happens for a bare wire or a stray surface rather than a solid.
    """
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


def face_from_wire(wire: Any) -> tuple[Any, bool]:
    """A face spanning ``wire``, and whether the loop was planar.

    A planar loop gives an exact face. A rim that follows a curved surface does not, so it
    is filled with a fitted surface instead -- adequate for closing a fluid domain, but
    reported, since a filled face is an approximation and the user should know the cap is
    not exactly the shape they drew.
    """
    try:
        return Part.Face(wire), True
    except Exception:  # noqa: BLE001 -- non-planar wires cannot make a planar face
        pass
    try:
        return Part.makeFilledFace(wire.Edges), False
    except Exception as exc:  # noqa: BLE001
        raise CapError(
            "could not build a surface across this loop. It is neither planar nor "
            "smooth enough to fill, which usually means the loop wanders over more than "
            "one opening -- try picking an edge on a simpler rim."
        ) from exc


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

    ``thickness`` is the total depth, half on each side of the opening, so the cap always
    crosses the surface it plugs regardless of which way round the part faces.

    Overlap is applied only to planar openings. A non-planar loop is spanned by a fitted
    surface whose centroid need not lie on it, and scaling about that point would push the
    cap off the rim rather than widen it.
    """
    if thickness <= 0.0:
        raise CapError(f"cap thickness must be positive, got {thickness} mm")

    face, planar = face_from_wire(wire)
    if planar:
        face = grow_face(face, overlap)

    normal = _normal_of(face)
    solid = face.extrude(normal * thickness)
    solid.translate(normal * (-thickness / 2.0))

    if not solid.Solids:
        raise CapError("extruding the cap outline produced no solid")
    return solid if planar else solid.removeSplitter()


def opening_from_wire(wire: Any, label: str, owner: Any = None) -> Opening:
    """Measure a loop: its true open area, and the edges it is made of."""
    face, planar = face_from_wire(wire)
    names = edge_names_in(owner, wire) if owner is not None else ()
    return Opening(
        label=label, wire=wire, edge_names=names, area_mm2=face.Area, planar=planar
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
            resolved.append((f"{_label(obj)}.{name}", owner, sub))

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
