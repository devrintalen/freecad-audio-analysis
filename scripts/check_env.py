#!/usr/bin/env python3
"""Report which parts of the development toolchain are present.

Run with plain system Python; it locates FreeCAD itself:

    python3 scripts/check_env.py

Each item is tagged with the capability tier that first needs it (see
STRUCTURE.md section 5), so a missing Tier 4 item is not a blocker for Tier 1 work.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

# Candidate locations for FreeCAD's Python bindings. Extend as needed.
FREECAD_LIB_CANDIDATES = (
    "/usr/lib64/freecad/lib64",
    "/usr/lib64/freecad/lib",
    "/usr/lib/freecad/lib64",
    "/usr/lib/freecad/lib",
    "/usr/share/freecad/lib",
)

OK, MISSING = "ok", "--"


def find_freecad_lib() -> str | None:
    """Return the directory containing FreeCAD.so, or None."""
    env = os.environ.get("FREECAD_LIB")
    candidates = (env,) + FREECAD_LIB_CANDIDATES if env else FREECAD_LIB_CANDIDATES
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "FreeCAD.so")):
            return path
    return None


def check_freecad() -> list[tuple[str, str, str, str]]:
    """Import FreeCAD in a subprocess so a hard crash cannot take us down."""
    lib = find_freecad_lib()
    if lib is None:
        return [("0", "FreeCAD", MISSING, "FreeCAD.so not found; set FREECAD_LIB")]

    probe = (
        "import sys, FreeCAD, femmesh.gmshtools, ObjectsFem;"
        "print('.'.join(FreeCAD.Version()[:3]), sys.version.split()[0])"
    )
    env = {**os.environ, "PYTHONPATH": lib}
    try:
        out = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=120, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [("0", "FreeCAD", MISSING, f"probe failed: {exc}")]

    if out.returncode != 0:
        detail = (out.stderr.strip().splitlines() or ["import failed"])[-1]
        return [("0", "FreeCAD", MISSING, detail)]

    version, fc_python = out.stdout.split()
    rows = [("0", "FreeCAD", OK, f"{version} at {lib}")]

    # The workbench must import third-party packages from inside FreeCAD. That only
    # works for free if FreeCAD embeds the same interpreter we install packages into.
    ours = ".".join(map(str, sys.version_info[:2]))
    theirs = ".".join(fc_python.split(".")[:2])
    if ours == theirs:
        note, status = f"Python {fc_python}, matches this interpreter", OK
    else:
        note = f"Python {fc_python} != system {sys.version.split()[0]} -- see docs/SETUP.md"
        status = MISSING
    rows.append(("0", "FreeCAD Python", status, note))
    return rows


def check_binary(tier: str, name: str, args: list[str], note: str) -> tuple[str, str, str, str]:
    path = shutil.which(name)
    if path is None:
        return (tier, name, MISSING, note)
    detail = path
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
        # Skip banner lines (ngspice opens with a row of asterisks).
        lines = [ln.strip() for ln in (out.stdout or out.stderr).splitlines()]
        useful = [ln for ln in lines if ln and ln.strip("*= ")]
        if useful:
            detail = f"{useful[0][:60]} ({path})"
    except (OSError, subprocess.SubprocessError):
        pass  # Present but unqueryable is still present.
    return (tier, name, OK, detail)


def check_module(tier: str, name: str, note: str) -> tuple[str, str, str, str]:
    if importlib.util.find_spec(name) is None:
        return (tier, name, MISSING, note)
    try:
        version = importlib.import_module(name).__version__
    except Exception:
        version = "installed"
    return (tier, name, OK, str(version))


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    rows += check_freecad()
    rows.append(check_binary("1", "ngspice", ["ngspice", "--version"], "emerge sci-electronics/ngspice"))
    rows.append(check_binary("2", "gmsh", ["gmsh", "--version"], "emerge sci-libs/gmsh (USE=python)"))
    rows.append(check_binary("2", "ElmerSolver", ["ElmerSolver", "-v"], "build from source; see docs/SETUP.md"))
    rows.append(check_binary("2", "ElmerGrid", ["ElmerGrid"], "ships with Elmer"))
    rows.append(check_binary("4", "NumCalc", ["NumCalc", "-h"], "build from Mesh2HRTF; see docs/SETUP.md"))

    for tier, mod, note in (
        ("1", "numpy", "emerge dev-python/numpy"),
        ("1", "scipy", "emerge dev-python/scipy"),
        ("1", "matplotlib", "emerge dev-python/matplotlib"),
        ("1", "pytest", "emerge dev-python/pytest"),
        ("1", "pyfar", "pip install pyfar"),
        ("3", "acoupy_ears", "pip install git+https://gitlab.com/acoupy/acoupy_ears.git"),
        ("4", "sofar", "pip install sofar"),
    ):
        rows.append(check_module(tier, mod, note))

    width = max(len(name) for _, name, _, _ in rows)
    print(f"{'tier':<5} {'component':<{width}} {'':<3} detail")
    print("-" * (width + 50))
    for tier, name, status, detail in rows:
        print(f"{tier:<5} {name:<{width}} {status:<3} {detail}")

    missing = [(t, n) for t, n, s, _ in rows if s == MISSING]
    blocking = [n for t, n in missing if t in ("0", "1")]
    print()
    if blocking:
        print(f"Blocking Tier 0/1 work: {', '.join(blocking)}")
    else:
        print("Tier 0 and Tier 1 development is unblocked.")
    if missing:
        print(f"{len(missing)} component(s) missing overall; see docs/SETUP.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
