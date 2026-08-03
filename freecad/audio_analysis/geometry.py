"""Reading acoustically meaningful quantities off FreeCAD geometry.

Tier 0 needs exactly one of these: the enclosed volume of a solid. That sounds trivial,
and as a computation it is -- but it is the first place the workbench crosses from CAD
into physics, so it is where the mm-to-SI discipline either holds or quietly fails.

It is also immediately useful. An enclosure's internal volume is the dominant parameter
in every sealed-box calculation, and reading it from the CAD model rather than retyping
it is one of the concrete advantages of living inside FreeCAD (STRUCTURE.md, Tier 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from freecad.audio_analysis.physics import units


@dataclass(frozen=True)
class VolumeMeasurement:
    """Enclosed volume of one object, carried in every unit anyone will ask for."""

    label: str
    volume_mm3: float
    solid_count: int

    @property
    def volume_m3(self) -> float:
        """Volume in SI cubic metres -- what solvers want."""
        return units.mm3_to_m3(self.volume_mm3)

    @property
    def volume_litres(self) -> float:
        """Volume in litres -- what loudspeaker engineers want."""
        return units.mm3_to_litre(self.volume_mm3)

    def describe(self) -> str:
        """One-line human-readable summary.

        Litres for enclosures, but small volumes get cubic centimetres instead: an
        earphone front cavity is ~0.001 litre, which reads as noise.
        """
        if self.volume_litres >= 0.1:
            magnitude = f"{self.volume_litres:.4g} litre"
        else:
            magnitude = f"{self.volume_mm3 / 1000.0:.4g} cm^3"
        plural = "s" if self.solid_count != 1 else ""
        return f"{self.label}: {magnitude} ({self.volume_m3:.6g} m^3, {self.solid_count} solid{plural})"


class NoSolidError(ValueError):
    """Raised when an object carries no solid whose volume could be measured."""


def measure_volume(obj: Any) -> VolumeMeasurement:
    """Measure the enclosed volume of a FreeCAD object's shape.

    Works for any object that exposes a shape, which in practice is all of them:
    ``Part`` primitives, ``PartDesign`` bodies, ``App::Link`` instances, ``App::Part``
    containers and assemblies. Containers report a compound of their children with
    placements applied, so an assembly returns its assembled total.

    Sums over all solids in the shape, so a multi-solid part reports its total. Raises
    :class:`NoSolidError` for anything without a solid -- a sketch, a bare surface, or an
    open shell -- because those have no enclosed volume, and silently returning zero
    would be indistinguishable from a genuinely empty cavity.

    Note that volume is invariant under rigid transforms, so it is correct regardless of
    where an object sits in an assembly. Positions are not: see
    :func:`global_placement_of`.
    """
    shape = getattr(obj, "Shape", None)
    label = getattr(obj, "Label", getattr(obj, "Name", "object"))

    if shape is None or shape.isNull():
        # An empty container is the common case here, and worth naming explicitly --
        # "has no shape" would send the user looking for a modelling error instead.
        if hasattr(obj, "Group"):
            raise NoSolidError(
                f"{label} is an empty container. Add geometry to it, or select the "
                f"solids inside it directly."
            )
        raise NoSolidError(f"{label} has no shape to measure")

    solids = getattr(shape, "Solids", [])
    if not solids:
        raise NoSolidError(
            f"{label} contains no solid. An acoustic cavity must be a closed solid; "
            f"a surface or open shell encloses no volume."
        )

    total_mm3 = sum(solid.Volume for solid in solids)
    label = getattr(obj, "Label", obj.Name)
    return VolumeMeasurement(label=label, volume_mm3=total_mm3, solid_count=len(solids))


class NoSubShapeError(ValueError):
    """Raised when a geometry reference cannot be resolved to faces or edges."""


def _resolve_subshapes(references: Any, kind: str) -> list[Any]:
    """Resolve an ``App::PropertyLinkSubList`` value to sub-shapes of ``kind``.

    ``references`` is FreeCAD's ``[(object, ("Face1", "Face3")), ...]`` form. An entry
    naming no sub-elements means the whole shape, so a user can reference a small helper
    solid instead of picking individual faces.
    """
    resolved: list[Any] = []
    for entry in references or []:
        try:
            obj, names = entry
        except (TypeError, ValueError) as exc:
            raise NoSubShapeError(f"malformed geometry reference: {entry!r}") from exc

        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise NoSubShapeError(f"{getattr(obj, 'Label', obj)} has no shape")

        if not names:
            resolved.extend(getattr(shape, kind + "s", []))
            continue

        for name in [names] if isinstance(names, str) else names:
            if not name:
                resolved.extend(getattr(shape, kind + "s", []))
                continue
            if not name.startswith(kind):
                raise NoSubShapeError(
                    f"{getattr(obj, 'Label', obj)}.{name} is not a {kind.lower()}"
                )
            try:
                resolved.append(shape.getElement(name))
            except Exception as exc:  # noqa: BLE001 -- stale reference after a rebuild
                raise NoSubShapeError(
                    f"{getattr(obj, 'Label', obj)}.{name} could not be resolved; the "
                    f"geometry may have changed since it was picked"
                ) from exc
    return resolved


def referenced_area_mm2(references: Any) -> float:
    """Total area of the referenced faces, in mm^2.

    Used where an acoustic element's area is a real feature of the model -- the open area
    of a vent, the area a damping mesh covers. Reading it from geometry means it tracks
    design changes instead of going stale the moment a hole is resized.
    """
    faces = _resolve_subshapes(references, "Face")
    if not faces:
        raise NoSubShapeError("no faces referenced")
    return sum(face.Area for face in faces)


def referenced_length_mm(references: Any) -> float:
    """Total length of the referenced edges, in mm.

    The natural way to give a leak its width: pick the edge loop where an earpad meets
    the head, rather than measuring a perimeter by hand.
    """
    edges = _resolve_subshapes(references, "Edge")
    if not edges:
        raise NoSubShapeError("no edges referenced")
    return sum(edge.Length for edge in edges)


def global_placement_of(obj: Any) -> Any:
    """The object's placement in global coordinates.

    Needed because of a trap that will bite from Tier 2 onward: a container's own
    ``Shape`` is already in global coordinates, but a **child's** ``Shape`` is expressed
    in the child's local frame. So a face picked on a part nested inside an assembly
    reports local coordinates, and a mesh or probe positioned from it would be silently
    displaced by the assembly transform.

    Volume is unaffected -- it is invariant under rigid transforms -- which is why Tier 0
    can ignore this. Anything positional must not.
    """
    if hasattr(obj, "getGlobalPlacement"):
        return obj.getGlobalPlacement()
    return getattr(obj, "Placement", None)


def measure_volumes(objects: Sequence[Any]) -> tuple[list[VolumeMeasurement], list[str]]:
    """Measure several objects, returning successes and per-object failure messages.

    Partial failure is normal when a user multi-selects: one sketch among five solids
    should not abort the whole measurement.
    """
    measured: list[VolumeMeasurement] = []
    problems: list[str] = []
    for obj in objects:
        try:
            measured.append(measure_volume(obj))
        except NoSolidError as exc:
            problems.append(str(exc))
    return measured, problems
