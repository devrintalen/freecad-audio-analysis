"""Finding the external solver binaries, and reporting honestly when they are absent.

Every physics kernel this workbench uses is a separate executable (STRUCTURE.md section 3).
Tier 1 needs none of them; later tiers need Gmsh, Elmer and NumCalc. A missing binary must
disable the commands that need it with a clear explanation -- never a traceback
(CLAUDE.md, "Missing binaries degrade gracefully").

Resolution order for each solver:

1. An explicit path the user set in the workbench preferences.
2. ``PATH``.

The result is cached, because this is consulted whenever a toolbar refreshes and
``shutil.which`` hits the filesystem. Call :func:`refresh` after changing preferences or
installing something.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

import FreeCAD

PREFERENCES_PATH = "User parameter:BaseApp/Preferences/Mod/AudioAnalysis"


@dataclass(frozen=True)
class SolverSpec:
    """A solver binary the workbench knows how to look for."""

    key: str
    binary: str
    #: Capability tier that first needs it, for messaging (STRUCTURE.md section 5).
    tier: int
    purpose: str
    install_hint: str

    @property
    def preference_key(self) -> str:
        """Parameter name holding a user-set override path."""
        return f"{self.key}Path"


SOLVERS: tuple[SolverSpec, ...] = (
    SolverSpec(
        key="Ngspice",
        binary="ngspice",
        tier=1,
        purpose="lumped equivalent-circuit solving",
        install_hint="emerge sci-electronics/ngspice",
    ),
    SolverSpec(
        key="Gmsh",
        binary="gmsh",
        tier=2,
        purpose="meshing",
        install_hint="emerge sci-libs/gmsh (USE=python,opencascade)",
    ),
    SolverSpec(
        key="ElmerSolver",
        binary="ElmerSolver",
        tier=2,
        purpose="3D acoustic, structural and coupled solves",
        install_hint="build from source; see docs/SETUP.md",
    ),
    SolverSpec(
        key="ElmerGrid",
        binary="ElmerGrid",
        tier=2,
        purpose="mesh conversion for Elmer",
        install_hint="ships with Elmer; see docs/SETUP.md",
    ),
    SolverSpec(
        key="NumCalc",
        binary="NumCalc",
        tier=4,
        purpose="exterior BEM for radiation and directivity",
        install_hint="build from Mesh2HRTF; see docs/SETUP.md",
    ),
)

SOLVERS_BY_KEY = {spec.key: spec for spec in SOLVERS}

_cache: dict[str, str | None] = {}


def _preference_override(spec: SolverSpec) -> str | None:
    """Return a user-configured path for ``spec``, if it is set and usable."""
    try:
        params = FreeCAD.ParamGet(PREFERENCES_PATH)
    except Exception:
        return None
    configured = params.GetString(spec.preference_key, "").strip()
    if not configured:
        return None
    if os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    FreeCAD.Console.PrintWarning(
        f"Audio Analysis: configured {spec.key} path '{configured}' is not an "
        f"executable file; falling back to PATH.\n"
    )
    return None


def find(key: str) -> str | None:
    """Return the path to a solver binary, or None if it cannot be found."""
    if key in _cache:
        return _cache[key]
    spec = SOLVERS_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"unknown solver {key!r}; known: {sorted(SOLVERS_BY_KEY)}")
    resolved = _preference_override(spec) or shutil.which(spec.binary)
    _cache[key] = resolved
    return resolved


def is_available(key: str) -> bool:
    """True if the named solver can be run."""
    return find(key) is not None


def set_path(key: str, path: str) -> None:
    """Persist an explicit path for a solver, overriding PATH lookup."""
    spec = SOLVERS_BY_KEY[key]
    FreeCAD.ParamGet(PREFERENCES_PATH).SetString(spec.preference_key, path)
    refresh()


def refresh() -> None:
    """Drop the cache so the next lookup re-scans."""
    _cache.clear()


def missing_message(key: str) -> str:
    """A user-facing explanation of why a command is unavailable."""
    spec = SOLVERS_BY_KEY[key]
    return (
        f"{spec.binary} was not found, so {spec.purpose} is unavailable "
        f"(needed from Tier {spec.tier}). Install it with: {spec.install_hint} -- "
        f"or set an explicit path in Edit > Preferences > Audio Analysis."
    )


def require(key: str) -> str:
    """Return a solver's path, raising a clear error if it is absent."""
    path = find(key)
    if path is None:
        raise RuntimeError(missing_message(key))
    return path


def status() -> list[tuple[SolverSpec, str | None]]:
    """Every known solver paired with its resolved path, for reporting."""
    return [(spec, find(spec.key)) for spec in SOLVERS]


def report(console: Any = None) -> None:
    """Log a one-line summary of solver availability to the FreeCAD console."""
    console = console or FreeCAD.Console
    found = [spec.binary for spec, path in status() if path]
    absent = [spec.binary for spec, path in status() if not path]
    console.PrintLog(f"Audio Analysis: solvers found: {', '.join(found) or 'none'}\n")
    if absent:
        console.PrintLog(f"Audio Analysis: solvers not found: {', '.join(absent)}\n")
