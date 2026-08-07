"""Finding the way out of a cavity that will not close.

When :mod:`~freecad.audio_analysis.cavity` reports that the seeded region reaches the
envelope wall, it has said everything geometry alone can say: this air is continuous with
the outside. It cannot say *where*, and on a real assembly that is the whole difficulty --
the opening is one feature among a few hundred, and a translucent solid filling the
bounding box looks the same whatever the cause.

Acoustically the distinction matters more than it looks. A leak is not a modelling
nuisance to be papered over: leakage dominates headphone bass, and a 1 mm slot around a
cup is a real acoustic element with a real impedance (STRUCTURE.md §2.4). The question is
always *which* it is -- a genuine port that should be modelled as a ``LeakPath``, or a
modelling slip where a cap missed. So this module locates it and leaves the judgement to
the user.

Two searches, deliberately different in cost, because they answer different questions.

**The near-miss scan** (:func:`near_miss_diagnostics`) never looks at the void at all. It
asks which *parts* come within a hair of each other and stop, which is what a leak looks
like before it is a leak, and which cap overlaps nothing at all. It is O(n^2) in the parts
but bounding-box pruned, and on a thirty-solid assembly it runs in about twenty seconds.
It found the real defect on ``assembly_driver_cup`` -- one vent cap displaced 1 mm, missing
its placement -- and named the object and the property to change.

**The neck-finder** (:func:`find_escape_path`) works on the air. It voxelises the leaking
region, computes how far every point of air sits from the nearest material, and finds the
route to the outside whose tightest point is widest. That tightest point is where the void
necks down, and twice its clearance is how wide the leak is. It costs about a minute on a
full assembly and points at a *location* rather than an object.

Prefer the scan. It is thirty times cheaper and its answer is actionable without further
interpretation. The neck-finder earns its place when the scan finds nothing -- an opening
nobody ever tried to cap has no near-miss signature, because there is no second part to
come close to.

**Why neither of these cuts the void.** OpenCascade cannot be trusted to subtract from a
region that itself came out of a boolean. On the two-way cup, ``region.cut(box)`` returns a
*negative volume* and reports the result valid; a sphere-shell sweep of the same region
reported 38.6 mm2 of open area between neighbouring radii of 8909 and 12763 mm2. Both
would have been read as findings. So the scan uses only ``common`` and ``distToShape``, and
the neck-finder uses only ``slice``, all of which behaved on the same geometry
(STRUCTURE.md §6.5).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import FreeCAD

from freecad.audio_analysis.cavity import BoundarySolid
from freecad.audio_analysis.checks import Diagnostic, Severity

#: Parts closer than this, in mm, are reported by the near-miss scan.
#:
#: Half a millimetre is comfortably above any gap that seals in practice and comfortably
#: below a clearance anyone designs on purpose. It is a search radius, not a physical
#: threshold: an opening of *any* size connects two regions, so the number only decides how
#: much gets listed.
DEFAULT_NEAR_MISS_MM = 0.5

#: Below this shared volume, in mm^3, two solids are not considered to overlap.
OVERLAP_EPSILON_MM3 = 1e-9

#: Voxel size for the neck-finder, in mm. Sets both the cost and the finest leak it can
#: resolve: a gap narrower than this is invisible to it, so a "no escape path" result is
#: never proof that a model is closed.
DEFAULT_RESOLUTION_MM = 0.75

#: Refuse to build a grid larger than this. A finer grid is quadratically slower to
#: rasterise and cubically larger in memory, and past this point the panel appears hung.
MAX_VOXELS = 6_000_000


class LeakSearchError(RuntimeError):
    """Raised when a leak search cannot run at all."""


# ---------------------------------------------------------------------------------
# The near-miss scan: which parts nearly touch, and which cap seals nothing.
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class NearMiss:
    """Two solids that do not overlap, with the gap between them."""

    a: str
    b: str
    gap_mm: float

    @property
    def is_coincident(self) -> bool:
        """Whether the two merely touch, rather than standing apart."""
        return self.gap_mm <= 1e-9


def _overlaps(a: Any, b: Any) -> bool:
    try:
        return a.common(b).Volume > OVERLAP_EPSILON_MM3
    except Exception:  # noqa: BLE001 -- a failed intersection is not evidence of overlap
        return False


def near_misses(
    sources: Sequence[BoundarySolid], *, limit_mm: float = DEFAULT_NEAR_MISS_MM
) -> list[NearMiss]:
    """Every pair of solids that comes within ``limit_mm`` without overlapping.

    Bounding boxes prune the pair list first, because ``distToShape`` between two detailed
    solids compares every face against every face and is far too slow to run on all of
    them.
    """
    found: list[NearMiss] = []
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            first, second = sources[i], sources[j]
            box = FreeCAD.BoundBox(first.solid.BoundBox)
            box.enlarge(limit_mm)
            if not box.intersect(second.solid.BoundBox):
                continue
            if _overlaps(first.solid, second.solid):
                continue
            try:
                gap = second.solid.distToShape(first.solid)[0]
            except Exception:  # noqa: BLE001 -- OCC reports distance failures as text
                continue
            if gap <= limit_mm:
                found.append(NearMiss(first.label, second.label, gap))
    found.sort(key=lambda m: (m.is_coincident, m.gap_mm))
    return found


def _sealing_nothing(
    sources: Sequence[BoundarySolid], caps: Iterable[str]
) -> list[str]:
    """Cap labels whose solid overlaps no other solid at all."""
    cap_labels = set(caps)
    idle: list[str] = []
    for index, source in enumerate(sources):
        if source.label not in cap_labels:
            continue
        others = [s for k, s in enumerate(sources) if k != index]
        box = FreeCAD.BoundBox(source.solid.BoundBox)
        box.enlarge(DEFAULT_NEAR_MISS_MM)
        near = [s for s in others if box.intersect(s.solid.BoundBox)]
        if not any(_overlaps(source.solid, s.solid) for s in near):
            idle.append(source.label)
    return idle


def near_miss_diagnostics(
    sources: Sequence[BoundarySolid],
    *,
    caps: Iterable[str] = (),
    limit_mm: float = DEFAULT_NEAR_MISS_MM,
) -> list[Diagnostic]:
    """Findings about parts that nearly touch, worst first.

    ``caps`` names the solids that exist only to close an opening. A cap is held to a
    stricter standard than an ordinary part: an ordinary pair that merely touches is a
    normal mating contact, whereas a cap that touches without overlapping has closed
    nothing, and a cap that overlaps *nothing at all* is not doing its job in any sense.
    """
    diagnostics: list[Diagnostic] = []
    cap_labels = set(caps)

    for label in _sealing_nothing(sources, cap_labels):
        diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="cap-seals-nothing",
                message="This cap does not overlap any part, so it closes nothing.",
                why=(
                    "A cap seals by overlapping the material around its opening. One that "
                    "merely reaches the opening leaves a seam, and one that stops short of "
                    "it leaves a gap -- and an opening of any size, however small, keeps "
                    "the cavity continuous with the outside. Compare this cap against its "
                    "siblings: if they were made the same way and they overlap while this "
                    "one does not, the cap is in the wrong place rather than the wrong "
                    "size, and growing it will not help."
                ),
                remedy=(
                    "Check this cap's Placement against a cap that works. Deleting it and "
                    "re-creating it from the same opening is usually quicker than "
                    "correcting the offset by hand."
                ),
                reference="STRUCTURE.md §6.5",
                subject=label,
            )
        )

    misses = near_misses(sources, limit_mm=limit_mm)
    gaps = [m for m in misses if not m.is_coincident]
    coincident = [m for m in misses if m.is_coincident]

    for miss in gaps:
        involves_cap = miss.a in cap_labels or miss.b in cap_labels
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING if involves_cap else Severity.INFO,
                code="parts-nearly-touch",
                message=(
                    f"{miss.a} and {miss.b} come within {miss.gap_mm:.4g} mm without "
                    f"ever meeting."
                ),
                why=(
                    "Two parts that approach this closely and stop leave a channel between "
                    "them, and a channel connects whatever is on either side of it. That "
                    "may be deliberate -- leakage dominates headphone bass, and a modelled "
                    "gap is often the honest thing -- but a gap this small is more often a "
                    "part that was meant to seal and did not."
                    + (
                        " One of these is a cap, which exists to close an opening, so this "
                        "one is worth looking at first."
                        if involves_cap
                        else ""
                    )
                ),
                remedy=(
                    "If the gap is real, model it as a LeakPath rather than closing it. If "
                    "it is not, make the two parts overlap."
                ),
                reference="STRUCTURE.md §6.5",
            )
        )

    if coincident:
        pairs = ", ".join(f"{m.a}/{m.b}" for m in coincident[:8])
        more = f" and {len(coincident) - 8} more" if len(coincident) > 8 else ""
        diagnostics.append(
            Diagnostic(
                severity=Severity.INFO,
                code="parts-coincident",
                message=f"{len(coincident)} pair(s) touch without overlapping: {pairs}{more}.",
                why=(
                    "Coincident faces are how mating parts are normally modelled and are "
                    "usually fine -- the union closes the seam. They are listed only "
                    "because a zero-thickness seam occasionally survives a boolean, and if "
                    "everything else has been ruled out this is where to look next."
                ),
                remedy="Nothing, unless the searches above came up empty.",
                reference="STRUCTURE.md §6.5",
            )
        )

    return diagnostics


def describe_near_misses(diagnostics: Sequence[Diagnostic]) -> str:
    """A short summary line for the panel, above the full findings."""
    if not diagnostics:
        return (
            "No part comes close to another without meeting it. Whatever the opening is, "
            "it has no second part beside it — so it is likely a hole nobody has capped "
            "rather than a cap that missed. Trace the leak path to find where it is."
        )
    sealing = [d for d in diagnostics if d.code == "cap-seals-nothing"]
    gaps = [d for d in diagnostics if d.code == "parts-nearly-touch"]
    if sealing:
        names = ", ".join(d.subject for d in sealing)
        return f"{names} overlaps nothing and so seals nothing. Start there."
    if gaps:
        return f"{len(gaps)} pair(s) come close without meeting. The likeliest cause is listed first."
    return "Only coincident mating faces, which are normal. Trace the leak path instead."


# ---------------------------------------------------------------------------------
# The neck-finder: where does the air pinch down on its way out?
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bottleneck:
    """The tightest point on the widest route from the seed to the outside."""

    #: Where the route is narrowest.
    point: tuple[float, float, float]
    #: Distance from that point to the nearest material, in mm.
    clearance_mm: float
    #: Voxel size the search ran at. Nothing finer than this can be seen.
    resolution_mm: float
    #: The route, as ``(x, y, z, clearance)`` samples.
    path: list[tuple[float, float, float, float]]
    #: Volume the voxel grid accounted for, against the region's own, both in mm^3. A
    #: large disagreement means the rasterisation missed something.
    voxel_volume_mm3: float
    region_volume_mm3: float

    @property
    def width_mm(self) -> float:
        """How wide the leak is: twice the clearance at its tightest."""
        return 2.0 * self.clearance_mm


def _grid_axes(box: Any, resolution: float) -> tuple[Any, Any, Any]:
    import numpy as np

    return (
        np.arange(box.XMin + resolution / 2.0, box.XMax, resolution),
        np.arange(box.YMin + resolution / 2.0, box.YMax, resolution),
        np.arange(box.ZMin + resolution / 2.0, box.ZMax, resolution),
    )


def voxelise(
    shape: Any, resolution: float, box: Any = None
) -> tuple[Any, Any, Any, Any]:
    """Rasterise ``shape`` onto a grid, plane by plane. Returns ``(air, xs, ys, zs)``.

    ``box`` is the volume to grid over, defaulting to ``shape``'s own bounding box. The
    caller should pass the *envelope*: gridding over the region instead makes every
    boundary voxel of an enclosed cavity part of the grid wall, so it would read as having
    escaped the moment the search started.

    Sectioning the solid and filling the section by the even-odd rule is what makes this
    affordable: it voxelised a 788 cm3 region at 0.75 mm in about seventy seconds, where
    classifying the same points with ``Shape.isInside`` needed roughly seven milliseconds
    each and would have taken two hours. The volumes agreed to about one percent, which
    :class:`Bottleneck` carries so the caller can check rather than assume.
    """
    import numpy as np

    try:
        from matplotlib.path import Path
    except ImportError as exc:  # noqa: BLE001 -- optional at import time, required here
        raise LeakSearchError(
            "tracing the leak path needs matplotlib, which is not importable from this "
            "interpreter. The near-miss scan does not need it."
        ) from exc

    xs, ys, zs = _grid_axes(box if box is not None else shape.BoundBox, resolution)
    if min(len(xs), len(ys), len(zs)) < 3:
        raise LeakSearchError(
            f"the region is too small for a {resolution} mm grid; it spans only "
            f"{len(xs)} x {len(ys)} x {len(zs)} voxels"
        )

    grid_x, grid_z = np.meshgrid(xs, zs, indexing="ij")
    plane = np.column_stack([grid_x.ravel(), grid_z.ravel()])
    air = np.zeros((len(xs), len(ys), len(zs)), dtype=bool)
    deflection = max(resolution / 10.0, 1e-3)

    for index, y in enumerate(ys):
        inside = np.zeros(len(plane), dtype=bool)
        try:
            wires = shape.slice(FreeCAD.Vector(0, 1, 0), float(y))
        except Exception:  # noqa: BLE001 -- a plane that will not section is empty here
            wires = []
        for wire in wires:
            try:
                points = wire.discretize(Deflection=deflection)
            except Exception:  # noqa: BLE001 -- a degenerate wire contributes nothing
                continue
            if len(points) < 3:
                continue
            loop = np.array([[p[0], p[2]] for p in points])
            inside ^= Path(loop).contains_points(plane)
        air[:, index, :] = inside.reshape(len(xs), len(zs))

    return air, xs, ys, zs


def find_escape_path(
    shape: Any,
    seed: Any,
    *,
    envelope_box: Any = None,
    resolution: float = DEFAULT_RESOLUTION_MM,
    max_voxels: int = MAX_VOXELS,
) -> Bottleneck | None:
    """The widest route from ``seed`` out of ``shape``, or ``None`` if there is none.

    "Widest" is the right notion, not "shortest": a leak is characterised by its narrowest
    point, so the route worth reporting is the one whose narrowest point is the *least*
    narrow. That is a maximum-capacity path, which a single priority-queue sweep solves
    exactly -- no threshold to choose and no binary search to converge.

    ``envelope_box`` bounds the search and defines what "out" means: reaching its surface
    is escaping. It defaults to ``shape``'s own bounding box, which is right for the
    exterior region -- that region *is* the envelope minus the parts -- and wrong for an
    enclosed one, so pass the envelope explicitly whenever the region might be closed.

    Returns ``None`` when no route reaches the boundary, which means the cavity is closed
    *at this resolution*. That is not proof it is closed: a gap thinner than ``resolution``
    cannot be represented and will read as sealed.
    """
    import numpy as np

    try:
        from scipy import ndimage
    except ImportError as exc:  # noqa: BLE001
        raise LeakSearchError(
            "tracing the leak path needs SciPy, which is not importable from this "
            "interpreter. The near-miss scan does not need it."
        ) from exc

    box = envelope_box if envelope_box is not None else shape.BoundBox
    spans = (box.XLength, box.YLength, box.ZLength)
    estimate = 1
    for span in spans:
        estimate *= max(int(span / resolution), 1)
    if estimate > max_voxels:
        resolution = resolution * (estimate / max_voxels) ** (1.0 / 3.0)

    air, xs, ys, zs = voxelise(shape, resolution, box)
    clearance = ndimage.distance_transform_edt(air, sampling=resolution)

    def index_of(point: Any) -> tuple[int, int, int]:
        return (
            int(round((point.x - xs[0]) / resolution)),
            int(round((point.y - ys[0]) / resolution)),
            int(round((point.z - zs[0]) / resolution)),
        )

    shape_ = air.shape
    start = index_of(seed)
    if not all(0 <= start[axis] < shape_[axis] for axis in range(3)):
        raise LeakSearchError("the seed point lies outside the region's bounding box")
    if not air[start]:
        # The seed sits within half a voxel of a wall often enough to be worth rescuing.
        neighbourhood = 2
        best = None
        for di in range(-neighbourhood, neighbourhood + 1):
            for dj in range(-neighbourhood, neighbourhood + 1):
                for dk in range(-neighbourhood, neighbourhood + 1):
                    cand = (start[0] + di, start[1] + dj, start[2] + dk)
                    if not all(0 <= cand[a] < shape_[a] for a in range(3)):
                        continue
                    if air[cand] and (best is None or clearance[cand] > clearance[best]):
                        best = cand
        if best is None:
            raise LeakSearchError(
                "the seed point is not in the air on this grid; the region may be thinner "
                "than the voxel size there"
            )
        start = best

    neighbours = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    best_capacity = np.zeros_like(clearance)
    parent: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    best_capacity[start] = clearance[start]
    heap = [(-clearance[start], start)]
    goal = None

    while heap:
        negative, current = heapq.heappop(heap)
        capacity = -negative
        if capacity < best_capacity[current]:
            continue
        i, j, k = current
        if (
            i in (0, shape_[0] - 1)
            or j in (0, shape_[1] - 1)
            or k in (0, shape_[2] - 1)
        ):
            goal = current
            break
        for di, dj, dk in neighbours:
            step = (i + di, j + dj, k + dk)
            if not all(0 <= step[a] < shape_[a] for a in range(3)):
                continue
            if not air[step]:
                continue
            widened = min(capacity, clearance[step])
            if widened > best_capacity[step]:
                best_capacity[step] = widened
                parent[step] = current
                heapq.heappush(heap, (-widened, step))

    if goal is None:
        return None

    route = [goal]
    while route[-1] != start:
        route.append(parent[route[-1]])
    route.reverse()

    tightest = min(route, key=lambda v: clearance[v])
    samples = [
        (float(xs[v[0]]), float(ys[v[1]]), float(zs[v[2]]), float(clearance[v]))
        for v in route
    ]
    return Bottleneck(
        point=(float(xs[tightest[0]]), float(ys[tightest[1]]), float(zs[tightest[2]])),
        clearance_mm=float(clearance[tightest]),
        resolution_mm=resolution,
        path=samples,
        voxel_volume_mm3=float(air.sum()) * resolution**3,
        region_volume_mm3=float(shape.Volume),
    )


def describe_escape_path(result: Bottleneck | None, resolution: float) -> str:
    """What the neck-finder found, in terms a reader can act on."""
    if result is None:
        return (
            f"No route to the outside at {resolution:.2f} mm resolution.\n\n"
            f"That is not proof the model is closed — a gap thinner than {resolution:.2f} "
            f"mm cannot be represented on this grid and reads as sealed. If the extraction "
            f"still says the cavity leaks, the opening is finer than the search can see; "
            f"the near-miss scan has no such limit and is the better tool for it."
        )

    x, y, z = result.point
    radius = (x * x + z * z) ** 0.5
    drift = abs(result.voxel_volume_mm3 - result.region_volume_mm3)
    share = 100.0 * drift / max(result.region_volume_mm3, 1.0)

    lines = [
        f"The widest way out is {result.width_mm:.2f} mm across at its tightest.",
        "",
        f"Narrowest point: ({x:.2f}, {y:.2f}, {z:.2f})  —  {radius:.2f} mm from the "
        f"z/x axis.",
        f"Clearance there: {result.clearance_mm:.2f} mm to the nearest material.",
        f"Grid: {result.resolution_mm:.2f} mm, accounting for "
        f"{result.voxel_volume_mm3 / 1000.0:.1f} cm³ against the region's "
        f"{result.region_volume_mm3 / 1000.0:.1f} cm³ ({share:.1f}% apart).",
        "",
        "Every other way out is narrower than this one, so this is the widest the leak "
        "can be. Sealing here is what closes the cavity — unless the gap is meant to be "
        "there, in which case it is a LeakPath.",
        "",
        "Route from the seed outward:",
    ]
    step = max(len(result.path) // 18, 1)
    for x_, y_, z_, clear in result.path[::step]:
        lines.append(f"   ({x_:8.2f},{y_:8.2f},{z_:8.2f})   {2.0 * clear:6.2f} mm wide")
    return "\n".join(lines)
