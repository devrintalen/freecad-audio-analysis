"""FreeCAD GUI entry point for the classic addon loader.

FreeCAD runs this file for addons laid out with entry points at the folder root. This
addon uses the ``freecad/audio_analysis/`` namespace layout, so FreeCAD normally takes
the *other* route and imports ``freecad.audio_analysis.init_gui`` instead -- which is
where the real registration lives. This file simply delegates, so the workbench appears
whichever loader an installation happens to use.

Registration is idempotent, so both loaders firing is harmless.
"""

import FreeCAD

try:
    from freecad.audio_analysis import init_gui

    init_gui.register()
except Exception as exc:  # noqa: BLE001 -- must not break FreeCAD startup
    import traceback

    FreeCAD.Console.PrintError(f"Audio Analysis workbench failed to load: {exc}\n")
    FreeCAD.Console.PrintLog(traceback.format_exc())
