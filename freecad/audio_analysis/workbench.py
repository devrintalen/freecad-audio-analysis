"""Workbench registration.

Only the toolbar and menu groups that have commands behind them are created. The full
nine-group layout in STRUCTURE.md section 6.3 arrives as the tiers land; showing empty
toolbars now would just be clutter.
"""

from __future__ import annotations

from typing import Any

import FreeCAD
import FreeCADGui

from freecad.audio_analysis import __version__, check_freecad_version, icon


class AudioAnalysisWorkbench(FreeCADGui.Workbench):
    """The Audio Analysis workbench."""

    MenuText = "Audio Analysis"
    ToolTip = "Acoustic simulation for headphones, earphones and loudspeakers"
    Icon = icon("AudioAnalysis")

    def Initialize(self) -> None:
        """Called once, the first time the user switches to this workbench."""
        ok, message = check_freecad_version()
        if not ok:
            FreeCAD.Console.PrintWarning(f"Audio Analysis: {message}\n")

        from freecad.audio_analysis.commands import (
            analysis_commands,
            measure_volume,
            network_commands,
        )
        from freecad.audio_analysis.solvers import discovery

        analysis = analysis_commands.register_all()
        tools = measure_volume.register_all()
        model, solve = network_commands.register_all()

        self.appendToolbar("Audio Analysis", analysis)
        self.appendToolbar("Audio Network", model)
        self.appendToolbar("Audio Solve", solve)
        self.appendToolbar("Audio Tools", tools)
        for group in (analysis, model, solve, tools):
            self.appendMenu("&Audio Analysis", group)

        discovery.report()
        FreeCAD.Console.PrintLog(f"Audio Analysis {__version__} loaded.\n")

    def Activated(self) -> None:
        """Re-scan for solvers on entry, so an install mid-session is picked up."""
        from freecad.audio_analysis.solvers import discovery

        discovery.refresh()

    def Deactivated(self) -> None:
        pass

    def GetClassName(self) -> str:
        # Required by FreeCAD: marks this as a Python workbench.
        return "Gui::PythonWorkbench"

    def ContextMenu(self, recipient: Any) -> None:
        pass
