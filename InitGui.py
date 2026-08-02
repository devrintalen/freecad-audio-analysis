"""FreeCAD GUI entry point for the Audio Analysis workbench.

FreeCAD executes this file at startup for every addon in its Mod directory. Keep it
minimal and failure-tolerant: an exception here breaks FreeCAD's whole startup sequence,
not just this workbench.
"""

import FreeCAD
import FreeCADGui

try:
    from freecad.audio_analysis.workbench import AudioAnalysisWorkbench

    FreeCADGui.addWorkbench(AudioAnalysisWorkbench())
except Exception as exc:  # noqa: BLE001 -- must not break FreeCAD startup
    import traceback

    FreeCAD.Console.PrintError(f"Audio Analysis workbench failed to load: {exc}\n")
    FreeCAD.Console.PrintLog(traceback.format_exc())
