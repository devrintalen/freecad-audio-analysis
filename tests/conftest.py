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


def _prefer_working_tree() -> None:
    """Make ``freecad.audio_analysis`` resolve to this repository, not an installed copy.

    ``import FreeCAD`` extends the ``freecad`` namespace package with every addon under
    its Mod directory. When this workbench is also installed there -- which it is during
    normal use -- tests would silently exercise the installed copy rather than the working
    tree, and a newly written module would appear missing until it had been pushed and
    pulled back.
    """
    try:
        import FreeCAD  # noqa: F401
        import freecad
    except ImportError:
        return

    local = os.path.join(REPO_ROOT, "freecad")
    if not os.path.isdir(local):
        return
    others = [p for p in freecad.__path__ if os.path.realpath(p) != os.path.realpath(local)]
    # Assign a plain list rather than mutating in place: ``freecad.__path__`` is a
    # dynamic PEP 420 _NamespacePath that recomputes itself from sys.path, so an in-place
    # reorder is silently discarded. Replacing it pins the order.
    freecad.__path__ = [local, *others]

    # Importing FreeCAD also *pre-registers* addon packages in sys.modules, so the
    # installed copy is already bound before the path reorder can take effect. Drop those
    # entries so the next import resolves against the working tree.
    for name in [n for n in sys.modules if n == "freecad.audio_analysis"
                 or n.startswith("freecad.audio_analysis.")]:
        del sys.modules[name]


_add_freecad_to_path()
_prefer_working_tree()
