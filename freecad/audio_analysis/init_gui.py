"""GUI entry point for the namespace-package addon loader.

FreeCAD has two ways of starting a workbench:

* the **classic** loader, which executes ``InitGui.py`` at the root of the addon folder;
* the **namespace** loader, which imports ``freecad.<package>.init_gui`` for any addon
  laid out as ``freecad/<package>/``.

This addon uses the ``freecad/audio_analysis/`` layout, so FreeCAD takes the namespace
route and looks for *this* file. Without it the workbench installs cleanly, imports
cleanly, and never appears in the selector -- the failure mode that prompted this module.
The root ``InitGui.py`` is kept as well so either loader works.

Registration is guarded so a second load cannot raise, and so any failure reports a
readable message instead of breaking FreeCAD's startup.
"""

import FreeCAD
import FreeCADGui

_WORKBENCH_NAME = "AudioAnalysisWorkbench"


def _already_registered() -> bool:
    """True if something has already registered this workbench.

    Both loaders can fire in some installations, and a duplicate registration is a
    warning at best and an exception at worst.
    """
    try:
        return _WORKBENCH_NAME in FreeCADGui.listWorkbenches()
    except Exception:  # noqa: BLE001 -- listWorkbenches is unavailable very early
        return False


def register() -> None:
    """Add the workbench to FreeCAD, unless it is already there."""
    if _already_registered():
        return
    try:
        from freecad.audio_analysis.workbench import AudioAnalysisWorkbench

        FreeCADGui.addWorkbench(AudioAnalysisWorkbench())
    except Exception as exc:  # noqa: BLE001 -- must never break FreeCAD startup
        import traceback

        FreeCAD.Console.PrintError(f"Audio Analysis workbench failed to load: {exc}\n")
        FreeCAD.Console.PrintLog(traceback.format_exc())


register()
