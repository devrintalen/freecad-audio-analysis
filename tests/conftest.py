"""Test configuration.

Two suites live side by side:

* Pure Python (``test_air.py``, ``test_units.py``, ``test_geometry_units.py``) -- runs in
  any interpreter, no FreeCAD needed.
* Integration (``test_freecad_integration.py``) -- needs FreeCAD's bindings, which are
  not on the default ``sys.path``. This file locates them and adds them, so
  ``python3 -m pytest`` just works from the repository root.

If FreeCAD cannot be found the integration tests skip rather than fail: the pure suite is
still worth running, and CI may not have FreeCAD available.
"""

from __future__ import annotations

import os
import sys

# Repository root, so `import freecad.audio_analysis` resolves against the working tree
# rather than an installed copy.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FREECAD_LIB_CANDIDATES = (
    os.environ.get("FREECAD_LIB", ""),
    "/usr/lib64/freecad/lib64",
    "/usr/lib64/freecad/lib",
    "/usr/lib/freecad/lib64",
    "/usr/lib/freecad/lib",
    "/usr/share/freecad/lib",
)


def _add_freecad_to_path() -> None:
    """Put FreeCAD's bindings on sys.path if they can be located."""
    try:
        import FreeCAD  # noqa: F401

        return  # Already importable.
    except ImportError:
        pass

    for candidate in FREECAD_LIB_CANDIDATES:
        if candidate and os.path.exists(os.path.join(candidate, "FreeCAD.so")):
            sys.path.append(candidate)
            return


_add_freecad_to_path()
