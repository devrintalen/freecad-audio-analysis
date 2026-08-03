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


class NewFromTemplate(AudioCommand):
    """Create an analysis pre-wired for a device type.

    Deciding which node a driver's back connects to is the most consequential and least
    visible choice in a lumped model, so the workbench offers correct topologies rather
    than a blank canvas (STRUCTURE.md §6.8).
    """

    Name = "NewFromTemplate"
    MenuText = "New analysis from template"
    ToolTip = (
        "Create an analysis already wired for a headphone, earphone or loudspeaker. "
        "Supplies a correct topology with plausible starting values."
    )
    IconName = "Template"

    def run(self) -> None:
        from freecad.audio_analysis.objects import make_analysis, make_environment
        from freecad.audio_analysis.templates import TEMPLATES

        key = self.ask_for_template(TEMPLATES)
        if key is None:
            return

        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
        with transaction("New analysis from template"):
            analysis = make_analysis(doc)
            make_environment(doc, analysis)
            from freecad.audio_analysis.templates import apply_template

            template = apply_template(key, doc, analysis)

        try:
            import FreeCADGui

            FreeCADGui.ActiveDocument.ActiveView.setActiveObject("AudioAnalysis", analysis)
        except Exception:  # noqa: BLE001 -- activation is a convenience, not a requirement
            pass

        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: created '{template.name}'.\n"
            f"  {template.summary}\n"
            f"  Next: {template.next_steps}\n"
        )

    def ask_for_template(self, templates) -> str | None:
        """Prompt for a template. Falls back to the first when no GUI is available."""
        try:
            from PySide import QtWidgets

            names = [t.name for t in templates]
            choice, accepted = QtWidgets.QInputDialog.getItem(
                None, "New acoustic analysis", "Device type:", names, 0, False
            )
            if not accepted:
                return None
            return templates[names.index(choice)].key
        except Exception:  # noqa: BLE001 -- headless or Qt unavailable
            return templates[0].key

    def IsActive(self) -> bool:
        return True


MODEL_COMMANDS = (AddVolume, AddNode, AddDriver, AddPort, AddResistance, AddLeak, AddRadiation)
SOLVE_COMMANDS = (AddFrequencySweep, AddLumpedSolver, RunLumpedSolver, PlotResults)
TEMPLATE_COMMANDS = (NewFromTemplate,)


def register_all() -> tuple[list[str], list[str], list[str]]:
    """Register each group, returning their command names."""
    templates = [register(cls()) for cls in TEMPLATE_COMMANDS]
    model = [register(cls()) for cls in MODEL_COMMANDS]
    solve = [register(cls()) for cls in SOLVE_COMMANDS]
    return templates, model, solve
