"""Commands for creating and populating an analysis."""

from __future__ import annotations

import FreeCAD
import FreeCADGui

from freecad.audio_analysis.commands.base import AudioCommand, register, transaction
from freecad.audio_analysis.objects import find_active_analysis, make_analysis, make_environment
from freecad.audio_analysis.solvers import discovery


class NewAnalysis(AudioCommand):
    """Create an analysis container, pre-populated with an Environment."""

    Name = "NewAnalysis"
    MenuText = "New acoustic analysis"
    ToolTip = (
        "Create an acoustic analysis container. Every study lives in one of these; "
        "a document may hold several so variants can be compared."
    )
    IconName = "AudioAnalysis"

    def run(self) -> None:
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
        with transaction("Create acoustic analysis"):
            analysis = make_analysis(doc)
            make_environment(doc, analysis)

        if FreeCADGui.ActiveDocument is not None:
            FreeCADGui.ActiveDocument.ActiveView.setActiveObject("AudioAnalysis", analysis)
        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: created {analysis.Label} with default air at 20 C.\n"
        )

    def IsActive(self) -> bool:
        return True  # Creates a document if none is open.


class AddEnvironment(AudioCommand):
    """Add an Environment to the active analysis."""

    Name = "AddEnvironment"
    MenuText = "Add environment"
    ToolTip = (
        "Add air properties (temperature, pressure, humidity) to the active analysis. "
        "Density, speed of sound and boundary-layer thickness are derived from them."
    )
    IconName = "Environment"

    def run(self) -> None:
        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no active analysis. Create one first, or double-click "
                "an existing analysis to activate it.\n"
            )
            return
        with transaction("Add environment"):
            env = make_environment(FreeCAD.ActiveDocument, analysis)
        FreeCAD.Console.PrintMessage(f"Audio Analysis: added {env.Label}.\n")

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class SolverStatus(AudioCommand):
    """Report which external solvers were found."""

    Name = "SolverStatus"
    MenuText = "Solver status"
    ToolTip = "Report which external solver binaries the workbench can find."
    IconName = "SolverStatus"

    def run(self) -> None:
        discovery.refresh()
        lines = ["Audio Analysis solver status:"]
        for spec, path in discovery.status():
            if path:
                lines.append(f"  found    {spec.binary:<12} {path}")
            else:
                lines.append(f"  missing  {spec.binary:<12} tier {spec.tier} -- {spec.install_hint}")
        FreeCAD.Console.PrintMessage("\n".join(lines) + "\n")

    def IsActive(self) -> bool:
        return True


def register_all() -> list[str]:
    return [register(cmd) for cmd in (NewAnalysis(), AddEnvironment(), SolverStatus())]
