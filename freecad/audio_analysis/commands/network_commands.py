"""Commands for building and solving a lumped network."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD

from freecad.audio_analysis.commands.base import AudioCommand, register, transaction
from freecad.audio_analysis.objects import find_active_analysis
from freecad.audio_analysis.objects import network_objects as no
from freecad.audio_analysis.objects import study


class _AddToAnalysis(AudioCommand):
    """Shared behaviour: create one object inside the active analysis."""

    factory: Callable[..., Any] = None
    object_name = "Object"

    def run(self) -> None:
        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no active analysis. Create one first, or double-click "
                "an existing analysis to activate it.\n"
            )
            return
        with transaction(f"Add {self.object_name}"):
            obj = type(self).factory(FreeCAD.ActiveDocument, analysis, self.object_name)
        FreeCAD.Console.PrintMessage(f"Audio Analysis: added {obj.Label}.\n")

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class AddVolume(_AddToAnalysis):
    Name, object_name, IconName = "AddVolume", "Volume", "Volume"
    MenuText = "Add acoustic volume"
    ToolTip = (
        "Add an enclosed volume of air. Acts as both a node and a compliance, and its "
        "volume can be measured from a CAD solid."
    )
    factory = staticmethod(no.make_volume)


class AddNode(_AddToAnalysis):
    Name, object_name, IconName = "AddNode", "Node", "Node"
    MenuText = "Add node"
    ToolTip = (
        "Add a junction with no volume. Needed where three elements meet, or to place a "
        "damping mesh in series with a vent."
    )
    factory = staticmethod(no.make_node)


class AddDriver(_AddToAnalysis):
    Name, object_name, IconName = "AddDriver", "Driver", "Driver"
    MenuText = "Add driver"
    ToolTip = (
        "Add a moving-coil driver from its Thiele-Small parameters. Connect its front "
        "and back nodes; several drivers per analysis is normal."
    )
    factory = staticmethod(no.make_driver)


class AddPort(_AddToAnalysis):
    Name, object_name, IconName = "AddPort", "Port", "Port"
    MenuText = "Add port or vent"
    ToolTip = "Add a duct or opening. End correction is applied automatically."
    factory = staticmethod(no.make_port)


class AddResistance(_AddToAnalysis):
    Name, object_name, IconName = "AddResistance", "Resistance", "Resistance"
    MenuText = "Add damping mesh"
    ToolTip = (
        "Add a resistive mesh or screen, specified in rayls. To damp a vent, put the "
        "mesh in series with it via a shared node -- not in parallel."
    )
    factory = staticmethod(no.make_resistance)


class AddLeak(_AddToAnalysis):
    Name, object_name, IconName = "AddLeak", "Leak", "Leak"
    MenuText = "Add leak path"
    ToolTip = (
        "Add a seal leak. Resistance goes as the inverse cube of the gap, so this "
        "usually dominates measured bass response."
    )
    factory = staticmethod(no.make_leak)


class AddRadiation(_AddToAnalysis):
    Name, object_name, IconName = "AddRadiation", "Radiation", "Radiation"
    MenuText = "Add radiation"
    ToolTip = "Terminate a node into free space with a piston radiation impedance."
    factory = staticmethod(no.make_radiation)


class AddFrequencySweep(_AddToAnalysis):
    Name, object_name, IconName = "AddFrequencySweep", "FrequencySweep", "Sweep"
    MenuText = "Add frequency sweep"
    ToolTip = "Set the frequencies the study is solved at."
    factory = staticmethod(study.make_frequency_sweep)


class AddLumpedSolver(_AddToAnalysis):
    Name, object_name, IconName = "AddLumpedSolver", "LumpedSolver", "Solve"
    MenuText = "Add lumped solver"
    ToolTip = "Add the lumped network solver."
    factory = staticmethod(study.make_lumped_solver)


class RunLumpedSolver(AudioCommand):
    """Validate, solve, and report a summary."""

    Name = "RunLumpedSolver"
    MenuText = "Solve"
    ToolTip = "Run the lumped network solver on the active analysis."
    IconName = "Solve"

    def run(self) -> None:
        from freecad.audio_analysis.checks import run_checks
        from freecad.audio_analysis.objects.study import LumpedSolver
        from freecad.audio_analysis.objects.base import is_audio_object

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return

        report = run_checks(analysis)
        if report.diagnostics:
            FreeCAD.Console.PrintMessage(report.format() + "\n")
        if not report.can_solve:
            FreeCAD.Console.PrintError(
                "Audio Analysis: solve blocked; resolve the errors above.\n"
            )
            return

        solvers = [o for o in analysis.Group if is_audio_object(o, LumpedSolver.Type)]
        if not solvers:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no solver in this analysis. Add a lumped solver first.\n"
            )
            return

        solver = solvers[0]
        solution = solver.Proxy.solve(solver, analysis)
        FreeCAD.Console.PrintMessage(f"Audio Analysis: {solver.Status}.\n")

        from freecad.audio_analysis.results.summary import summarise_solution

        FreeCAD.Console.PrintMessage(summarise_solution(solution, analysis) + "\n")

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class PlotResults(AudioCommand):
    """Plot the most recent solve."""

    Name = "PlotResults"
    MenuText = "Plot results"
    ToolTip = "Plot SPL, impedance and excursion from the most recent solve."
    IconName = "Plot"

    def run(self) -> None:
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.study import LumpedSolver
        from freecad.audio_analysis.results.plotting import plot_solution

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return
        solvers = [o for o in analysis.Group if is_audio_object(o, LumpedSolver.Type)]
        solution = solvers[0].Proxy.solution if solvers else None
        if solution is None:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no results yet. Solve first -- results are recomputed "
                "on demand rather than stored in the document.\n"
            )
            return
        plot_solution(solution, analysis)

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


MODEL_COMMANDS = (AddVolume, AddNode, AddDriver, AddPort, AddResistance, AddLeak, AddRadiation)
SOLVE_COMMANDS = (AddFrequencySweep, AddLumpedSolver, RunLumpedSolver, PlotResults)


def register_all() -> tuple[list[str], list[str]]:
    """Register both groups, returning their command names."""
    model = [register(cls()) for cls in MODEL_COMMANDS]
    solve = [register(cls()) for cls in SOLVE_COMMANDS]
    return model, solve
