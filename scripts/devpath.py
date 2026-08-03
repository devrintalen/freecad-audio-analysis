"""Make ``freecad.audio_analysis`` resolve to this working tree.

Needed by anything run from the repository — tests, benchmarks, examples — whenever the
workbench is *also* installed as an addon, which during normal use it is.

``freecad`` is a PEP 420 namespace package, and FreeCAD's site-packages bootstrap extends
it with every addon under the user's ``Mod`` directory, prepending them to ``sys.path``.
Putting the repository first in ``sys.path`` is therefore not enough: the namespace has
already been assembled from somewhere else. Without this, a module written five minutes ago
appears not to exist until it has been committed, pushed, and pulled back into the addon
directory — a confusing failure that costs more time than the fix.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FREECAD_LIB_CANDIDATES = (
    os.environ.get("FREECAD_LIB", ""),
    "/usr/lib64/freecad/lib64",
    "/usr/lib64/freecad/lib",
    "/usr/lib/freecad/lib64",
    "/usr/lib/freecad/lib",
    "/usr/share/freecad/lib",
)


def add_freecad_to_path() -> bool:
    """Put FreeCAD's bindings on ``sys.path`` if they can be located."""
    try:
        import FreeCAD  # noqa: F401

        return True
    except ImportError:
        pass

    for candidate in FREECAD_LIB_CANDIDATES:
        if candidate and os.path.exists(os.path.join(candidate, "FreeCAD.so")):
            sys.path.append(candidate)
            return True
    return False


def prefer_working_tree() -> None:
    """Pin the ``freecad`` namespace and drop any pre-bound installed copy."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    local = os.path.join(REPO_ROOT, "freecad")
    if not os.path.isdir(local):
        return
    try:
        import freecad
    except ImportError:
        return

    others = [p for p in freecad.__path__ if os.path.realpath(p) != os.path.realpath(local)]
    # Assign a plain list rather than mutating in place: ``freecad.__path__`` is a dynamic
    # _NamespacePath that recomputes itself from sys.path, so an in-place reorder is
    # silently discarded. Replacing it pins the order.
    freecad.__path__ = [local, *others]

    # The bootstrap also *pre-registers* addon packages in sys.modules, so the installed
    # copy is already bound before the path reorder can take effect. Drop those entries so
    # the next import resolves against the working tree.
    for name in [
        n for n in sys.modules
        if n == "freecad.audio_analysis" or n.startswith("freecad.audio_analysis.")
    ]:
        del sys.modules[name]


def setup(require_freecad: bool = False) -> bool:
    """Do both, in the order that works. Returns whether FreeCAD's bindings are available."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    available = add_freecad_to_path()
    prefer_working_tree()
    if require_freecad and not available:
        raise ImportError("FreeCAD's Python bindings could not be located")
    return available
