"""Audio Analysis workbench for FreeCAD.

Electroacoustic simulation for headphones, earphones and loudspeakers. See STRUCTURE.md
for the capability plan and CLAUDE.md for development conventions.

This module must stay importable without ``FreeCADGui`` so the physics and document
layers can be tested headlessly.
"""

from __future__ import annotations

import os

__version__ = "0.1.0"

#: Directory of the installed workbench, used to locate icons and resources.
MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
RESOURCES_PATH = os.path.join(MODULE_PATH, "resources")
ICONS_PATH = os.path.join(RESOURCES_PATH, "icons")

#: Minimum FreeCAD version this workbench is developed against. FEM internals moved
#: substantially in 1.0, and we wrap them (CLAUDE.md, "Reuse what FreeCAD provides").
MINIMUM_FREECAD_VERSION = (1, 0)


def icon(name: str) -> str:
    """Absolute path to a named SVG icon."""
    return os.path.join(ICONS_PATH, f"{name}.svg")


def check_freecad_version() -> tuple[bool, str]:
    """Check the running FreeCAD against :data:`MINIMUM_FREECAD_VERSION`.

    Returns ``(ok, message)`` rather than raising: an old FreeCAD should produce a clear
    warning at load, not a broken workbench with a traceback in the report view.
    """
    import FreeCAD

    try:
        version = tuple(int(part) for part in FreeCAD.Version()[:2])
    except (ValueError, TypeError):
        return True, ""  # Unparseable version: assume fine rather than block the user.

    if version < MINIMUM_FREECAD_VERSION:
        wanted = ".".join(str(p) for p in MINIMUM_FREECAD_VERSION)
        running = ".".join(str(p) for p in version)
        return False, (
            f"Audio Analysis needs FreeCAD {wanted} or newer; this is {running}. "
            f"The workbench will load but parts of it will not work."
        )
    return True, ""
