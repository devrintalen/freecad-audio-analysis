#!/usr/bin/env python3
"""Report what an existing FreeCAD model offers an acoustic analysis.

Run against any document -- a single part, a PartDesign body, or a full assembly with
external links -- to see what the workbench can read from it before committing to a
simulation setup:

    PYTHONPATH=/usr/lib64/freecad/lib64 python3 examples/inspect_assembly.py <file.FCStd>

Reports the parts and their volumes, the overall envelope, the element size the audio
band demands, and -- with ``--cavity`` -- whether a fluid domain can be extracted by
subtracting the parts from a bounding solid.

The cavity check is the interesting one. What gets simulated is the *air*, not the parts
(STRUCTURE.md 6.5), and an open cup or a ported box has no closed interior void until its
opening is capped. This script tells you which case you are in.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from freecad.audio_analysis.geometry import measure_volume, measure_volumes  # noqa: E402
from freecad.audio_analysis.physics import air  # noqa: E402

#: Object types that represent real geometry rather than datums or joints.
GEOMETRY_TYPES = ("App::Link", "Part::Feature", "Part::FeaturePython", "PartDesign::Body")

#: Ignore slivers left by boolean operations.
MIN_SOLID_VOLUME_MM3 = 1e-6


def collect_solids(doc: App.Document) -> list[Part.Solid]:
    """Every valid solid in the document, with placements applied.

    Datum planes and joint objects carry null or nonsense shapes -- an unbounded plane
    reports an absurd volume -- and a boolean against them fails outright, so they are
    filtered here rather than at the point of use.
    """
    solids: list[Part.Solid] = []
    for obj in doc.Objects:
        if obj.TypeId not in GEOMETRY_TYPES:
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        solids.extend(
            s for s in shape.Solids if s.isValid() and s.Volume > MIN_SOLID_VOLUME_MM3
        )
    return solids


def report_parts(doc: App.Document) -> None:
    parts = [o for o in doc.Objects if o.TypeId in GEOMETRY_TYPES]
    measured, problems = measure_volumes(parts)
    measured.sort(key=lambda m: -m.volume_mm3)

    print(f"\nParts ({len(measured)} measurable, {len(problems)} skipped)")
    print(f"  {'label':<32} {'volume':>12}  solids")
    for m in measured:
        print(f"  {m.label[:32]:<32} {m.volume_mm3 / 1000:>9.2f} cm3  {m.solid_count}")
    total = sum(m.volume_mm3 for m in measured)
    print(f"  {'TOTAL MATERIAL':<32} {total / 1000:>9.2f} cm3")

    for problem in problems:
        print(f"  skipped: {problem}")


def report_envelope(doc: App.Document) -> None:
    roots = [o for o in doc.RootObjects if getattr(o, "Shape", None) is not None]
    if not roots:
        return
    root = roots[0]
    try:
        measurement = measure_volume(root)
    except Exception as exc:  # noqa: BLE001
        print(f"\nRoot object {root.Label}: {exc}")
        return

    bb = root.Shape.BoundBox
    print(f"\nRoot object: {root.Label} ({root.TypeId})")
    print(f"  {measurement.describe()}")
    print(f"  bounding box: {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm")


def report_mesh_requirements(largest_dimension_mm: float) -> None:
    props = air.AirProperties.at()
    print(f"\nMesh sizing at {air.to_celsius(props.temperature):.0f} C "
          f"(c = {props.speed_of_sound:.1f} m/s)")
    for frequency in (1000.0, 5000.0, 10000.0, 20000.0):
        h_mm = props.mesh_size_for(frequency) * 1000.0
        spans = largest_dimension_mm / h_mm
        print(f"  {frequency / 1000:>5.0f} kHz: {h_mm:>6.2f} mm elements "
              f"({spans:.0f} across the model)")
    print(f"  viscous boundary layer at 1 kHz: "
          f"{props.viscous_boundary_layer(1000.0) * 1e6:.0f} um -- any gap near this "
          f"width is loss-dominated")


def report_cavity(doc: App.Document, padding_mm: float) -> None:
    """Try to extract a fluid domain by subtracting the parts from a bounding solid."""
    solids = collect_solids(doc)
    if not solids:
        print("\nNo solids found; cannot attempt cavity extraction.")
        return

    print(f"\nCavity extraction ({len(solids)} solids)")

    start = time.time()
    fused = solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]
    print(f"  fuse: {time.time() - start:.1f} s -> {len(fused.Solids)} solids, "
          f"{fused.Volume / 1000:.1f} cm3 of material")

    bb = fused.BoundBox
    envelope = Part.makeBox(
        bb.XLength + 2 * padding_mm,
        bb.YLength + 2 * padding_mm,
        bb.ZLength + 2 * padding_mm,
        App.Vector(bb.XMin - padding_mm, bb.YMin - padding_mm, bb.ZMin - padding_mm),
    )

    start = time.time()
    void = envelope.cut(fused)
    voids = sorted(void.Solids, key=lambda s: -s.Volume)
    print(f"  cut:  {time.time() - start:.1f} s -> {len(voids)} void regions")

    for i, solid in enumerate(voids[:8]):
        b = solid.BoundBox
        print(f"    void {i}: {solid.Volume / 1000:>8.2f} cm3  "
              f"bbox {b.XLength:.1f} x {b.YLength:.1f} x {b.ZLength:.1f} mm")

    # If the largest void spans the padded envelope, interior and exterior air are one
    # connected region -- the model is open, and the opening must be capped before any
    # enclosed volume exists.
    largest = voids[0]
    open_to_outside = (
        largest.BoundBox.XLength >= bb.XLength + padding_mm
        and largest.BoundBox.YLength >= bb.YLength + padding_mm
    )
    print()
    if open_to_outside:
        print("  The largest void reaches the envelope boundary: interior and exterior air")
        print("  are connected, so there is no closed cavity as the model stands.")
        print()
        print("  This does NOT mean you must model an ear or a head. The ear is a load,")
        print("  not a geometry: add one flat cap face across the opening and give it the")
        print("  ear's acoustic impedance. That is what an IEC 60318-1 artificial ear is")
        print("  physically. Ear or pinna geometry is only needed when cavity shape starts")
        print("  to matter -- above roughly 1 kHz for an over-ear cup -- or when the")
        print("  question is about seal, leakage or isolation. See STRUCTURE.md 6.4.")
        print()
        print("  If capping the obvious opening still leaves the void connected, the")
        print("  remaining paths are gaps between parts. Those may be real leaks, which")
        print("  dominate headphone bass, so decide per gap: close it, or declare it a")
        print("  LeakPath with an impedance.")
    else:
        print("  The largest void is fully enclosed and can serve as a fluid domain.")
    sealed = [s for s in voids[1:] if s.Volume > 1.0]
    if sealed:
        print(f"  Plus {len(sealed)} smaller sealed pockets (screw holes and similar), "
              f"acoustically negligible.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", help="path to a .FCStd file")
    parser.add_argument("--cavity", action="store_true",
                        help="attempt fluid-domain extraction (slow: boolean operations)")
    parser.add_argument("--padding", type=float, default=2.0,
                        help="envelope padding in mm for cavity extraction")
    args = parser.parse_args()

    if not os.path.exists(args.document):
        print(f"No such file: {args.document}", file=sys.stderr)
        return 1

    doc = App.openDocument(args.document)
    print(f"Document: {doc.Name}  ({len(doc.Objects)} objects)")

    external = [name for name in App.listDocuments() if name != doc.Name]
    if external:
        print(f"Linked documents auto-loaded: {', '.join(sorted(external))}")

    report_parts(doc)
    report_envelope(doc)

    roots = [o for o in doc.RootObjects if getattr(o, "Shape", None) is not None]
    if roots and not roots[0].Shape.isNull():
        bb = roots[0].Shape.BoundBox
        report_mesh_requirements(max(bb.XLength, bb.YLength, bb.ZLength))

    if args.cavity:
        report_cavity(doc, args.padding)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
