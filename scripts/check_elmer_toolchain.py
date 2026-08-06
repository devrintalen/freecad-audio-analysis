#!/usr/bin/env python3
"""Prove the Gmsh + Elmer toolchain end to end, before anything depends on it.

`check_env.py` answers "are the binaries present". This answers the much more useful
question: *do they actually work together, driven from FreeCAD, and produce a right
answer.* Run it once after installing Elmer and Gmsh, and again whenever either is
upgraded.

It deliberately uses a **stock FEM equation (heat), not acoustics**. The point is to
separate "our SIF generator is wrong" from "Elmer is misconfigured" — a distinction that
is cheap to establish now and expensive to untangle later. Nothing here touches
`freecad.audio_analysis`; it exercises FreeCAD's own Elmer integration, which is the
machinery §3 of STRUCTURE.md says we wrap rather than reimplement.

The test problem is steady conduction along a bar with both ends held at fixed
temperature. That has an exact answer — a linear profile — so we can check the field
pointwise rather than merely observing that the solver exited zero.

## The units check, and why it is not optional

FreeCAD exports its mesh in **millimetres** but writes material properties in **SI**. It
reconciles the two with `Coordinate Scaling = Real 0.001` in the SIF's Simulation block.
Omit that line and the model is a geometry 1000x too large.

Steady-state conduction between two Dirichlet faces is *scale-invariant*: with no source
term, stretching the bar changes nothing about the temperature profile. Deleting the
scaling line from the generated deck and re-solving gives a bit-identical field — measured,
not assumed. So the physics check below **cannot** catch a missing scale factor, and a
green benchmark would give false confidence.

That matters for Tier 2, where the same mistake is fatal rather than invisible: the
Helmholtz equation compares a wavelength against the geometry, so a 1000x scale error moves
every resonance by 1000x. It would not crash. It would just be wrong — exactly the failure
mode the units rule in CLAUDE.md exists to prevent.

Hence the scaling assertion is made against the **text of the SIF**, independently of the
solved result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devpath  # noqa: E402

devpath.setup()

# Bar dimensions in mm, and the two end temperatures in K. The length is what makes the
# scale error detectable in principle; the temperatures give an exact linear profile.
LENGTH_MM = 100.0
T_COLD_K = 300.0
T_HOT_K = 400.0

# The analytic field is exact, so the only error is the linear solve. Anything above a
# millikelvin means something is wrong with the solver, not with meshing resolution.
TOLERANCE_K = 1e-3

OK, FAIL = "ok ", "FAIL"


def _require_binaries() -> list[str]:
    """Report missing binaries rather than dying inside a subprocess call."""
    return [b for b in ("gmsh", "ElmerGrid", "ElmerSolver") if shutil.which(b) is None]


def _build_case(doc, workdir: str):
    """Assemble the FreeCAD analysis: bar, material, two fixed-temperature ends, mesh."""
    import ObjectsFem
    from femmesh.gmshtools import GmshTools

    box = doc.addObject("Part::Box", "Bar")
    box.Length, box.Width, box.Height = LENGTH_MM, 20, 20
    doc.recompute()

    analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
    solver = ObjectsFem.makeSolverElmer(doc, "SolverElmer")
    analysis.addObject(solver)
    ObjectsFem.makeEquationHeat(doc, solver)

    material = ObjectsFem.makeMaterialSolid(doc, "Material")
    card = material.Material
    card["Name"] = "Steel-Generic"
    # The heat writer refuses to proceed without all three of these.
    card["Density"] = "7900 kg/m^3"
    card["ThermalConductivity"] = "43.27 W/m/K"
    card["SpecificHeat"] = "500 J/kg/K"
    material.Material = card
    analysis.addObject(material)

    # Face1 and Face2 are the two ends of a Part::Box, normal to its Length.
    for name, face, kelvin in (("Cold", "Face1", T_COLD_K), ("Hot", "Face2", T_HOT_K)):
        constraint = ObjectsFem.makeConstraintTemperature(doc, name)
        constraint.References = [(box, face)]
        constraint.Temperature = f"{kelvin} K"
        analysis.addObject(constraint)

    doc.recompute()

    mesh_obj = ObjectsFem.makeMeshGmsh(doc, "Mesh")
    mesh_obj.Shape = box
    mesh_obj.CharacteristicLengthMax = "8 mm"
    analysis.addObject(mesh_obj)
    doc.recompute()

    error = GmshTools(mesh_obj).create_mesh()
    if error:
        raise RuntimeError(f"Gmsh meshing failed: {error}")

    # FreeCAD's own Elmer driver raises on a group-less mesh, because boundary conditions
    # are matched to Elmer bodies by group name. Catch it here with a clearer message.
    if not mesh_obj.FemMesh.Groups:
        raise RuntimeError(
            "Mesh has no groups — Elmer cannot bind boundary conditions. "
            "The constraints must exist in the analysis before meshing."
        )

    solver.WorkingDirectory = workdir
    return solver, mesh_obj


def _write_and_solve(solver, mesh_obj, workdir: str) -> str:
    """Write the deck, convert the mesh, run Elmer. Returns the SIF text."""
    from femsolver.elmer import writer as elmer_writer

    elmer_writer.Writer(solver, workdir).write_solver_input()

    mesh_file = os.path.join(workdir, "mesh.unv")
    mesh_obj.FemMesh.write(mesh_file)

    # "8 2" is unv in, Elmer out. This mirrors what FreeCAD's ElmerTools.prepare() runs,
    # but through subprocess rather than QProcess so the check stays headless and Qt-free.
    grid = subprocess.run(
        ["ElmerGrid", "8", "2", mesh_file, "-out", workdir],
        cwd=workdir, capture_output=True, text=True,
    )
    if grid.returncode != 0:
        raise RuntimeError(f"ElmerGrid failed:\n{grid.stdout[-2000:]}\n{grid.stderr[-2000:]}")

    solve = subprocess.run(["ElmerSolver"], cwd=workdir, capture_output=True, text=True)
    if solve.returncode != 0:
        raise RuntimeError(f"ElmerSolver failed:\n{solve.stdout[-3000:]}\n{solve.stderr[-2000:]}")

    with open(os.path.join(workdir, "case.sif")) as handle:
        return handle.read()


def _read_field(vtu_path: str) -> tuple[list[float], list[float]]:
    """Pull the temperature field and nodal x-coordinates out of Elmer's .vtu."""
    root = ET.parse(vtu_path).getroot()
    temperature, coordinates = None, None
    for piece in root.iter("Piece"):
        for block in piece.iter("PointData"):
            for array in block.iter("DataArray"):
                if array.get("Name") == "temperature":
                    temperature = [float(v) for v in array.text.split()]
        for block in piece.iter("Points"):
            for array in block.iter("DataArray"):
                coordinates = [float(v) for v in array.text.split()]
    if temperature is None or coordinates is None:
        raise RuntimeError(f"No temperature field in {vtu_path}")
    return temperature, coordinates[0::3]


def main() -> int:
    missing = _require_binaries()
    if missing:
        print(f"Cannot run: missing {', '.join(missing)}. See docs/SETUP.md.")
        return 1

    if not devpath.add_freecad_to_path():
        print("Cannot run: FreeCAD's Python bindings could not be located.")
        return 1

    import FreeCAD

    workdir = tempfile.mkdtemp(prefix="elmer-toolchain-")
    keep = "--keep" in sys.argv
    doc = FreeCAD.newDocument("elmer_toolchain_check")
    results = []

    try:
        solver, mesh_obj = _build_case(doc, workdir)
        results.append((OK, "gmsh", f"{mesh_obj.FemMesh.NodeCount} nodes, "
                                    f"{len(mesh_obj.FemMesh.Groups)} groups"))

        sif = _write_and_solve(solver, mesh_obj, workdir)
        results.append((OK, "elmer", "ElmerGrid + ElmerSolver completed"))

        # Checked against the deck text, not the result: see the module docstring for why
        # the physics check below is blind to this.
        if "Coordinate Scaling = Real 0.001" in sif:
            results.append((OK, "mm->m scaling", "Coordinate Scaling = Real 0.001 present"))
        else:
            results.append((FAIL, "mm->m scaling",
                            "Coordinate Scaling missing — mesh is mm, materials are SI. "
                            "A Helmholtz solve would put every resonance out by 1000x."))

        temperature, xs = _read_field(os.path.join(workdir, "FreeCAD_t0001.vtu"))
        span = max(xs) - min(xs)
        origin = min(xs)
        worst = max(
            abs(t - (T_COLD_K + (T_HOT_K - T_COLD_K) * (x - origin) / span))
            for t, x in zip(temperature, xs)
        )
        status = OK if worst <= TOLERANCE_K else FAIL
        results.append((status, "linear profile",
                        f"max deviation {worst:.2e} K over {len(temperature)} nodes "
                        f"(tolerance {TOLERANCE_K:g} K)"))

    except Exception as exc:  # noqa: BLE001 — the report is the product; show the reason
        results.append((FAIL, "toolchain", str(exc)))
    finally:
        FreeCAD.closeDocument(doc.Name)

    width = max(len(name) for _, name, _ in results)
    print(f"{'':<5}{'check':<{width}}  detail")
    print("-" * (width + 50))
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width}}  {detail}")
    print()

    failed = [name for status, name, _ in results if status == FAIL]
    if failed:
        print(f"Toolchain NOT proven: {', '.join(failed)}. Case kept at {workdir}")
        return 1

    if keep:
        print(f"Toolchain proven. Case kept at {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
        print("Toolchain proven end to end. Re-run with --keep to inspect the case files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
