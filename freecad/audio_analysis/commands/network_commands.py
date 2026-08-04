"""Commands for building and solving a lumped network."""

from __future__ import annotations

from typing import Any, Callable

import FreeCAD

from freecad.audio_analysis.commands.base import AudioCommand, register, transaction
from freecad.audio_analysis.objects import crossover, find_active_analysis, parameter_sweep, study
from freecad.audio_analysis.objects import network_objects as no
from freecad.audio_analysis.objects.base import is_audio_object


def _unresolved_selection() -> list[Any]:
    """The selection with sub-element paths left intact, not resolved into the part.

    ``getSelectionEx()`` defaults to ``resolve=1``, which walks a pick inside an assembly
    all the way down to the object that actually owns the face or edge -- typically a body
    in a *different* document, reported with a bare ``"Edge148"``. That throws away the
    assembly context: the sub-shape then comes back in the part's own frame rather than the
    assembled one, and anything built from it lands wherever the part was modelled instead
    of where it sits in the product.

    ``resolve=0`` keeps the top-level object in the active document together with the full
    ``"Body004.PolarPattern001.Edge148"`` path, which ``capping.resolve_reference`` resolves
    through ``getSubObject`` with the assembly transform applied.
    """
    import FreeCADGui

    try:
        return FreeCADGui.Selection.getSelectionEx("", 0)
    except (TypeError, AttributeError):  # older signature; still better than nothing
        return FreeCADGui.Selection.getSelectionEx()


def _selected_of_type(type_name: str) -> list[Any]:
    """Selected objects of one Audio type, or an empty list when there is no GUI."""
    try:
        import FreeCADGui

        return [
            o for o in FreeCADGui.Selection.getSelection() if is_audio_object(o, type_name)
        ]
    except (ImportError, AttributeError):
        return []


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


class AddPassiveRadiator(_AddToAnalysis):
    Name, object_name, IconName = "AddPassiveRadiator", "PassiveRadiator", "Driver"
    MenuText = "Add passive radiator"
    ToolTip = (
        "Add a driverless diaphragm: mass and compliance, no motor. Used where a port "
        "would have to be impractically long, which in a shallow enclosure is usual."
    )
    factory = staticmethod(no.make_passive_radiator)


class AddRadiation(_AddToAnalysis):
    Name, object_name, IconName = "AddRadiation", "Radiation", "Radiation"
    MenuText = "Add radiation"
    ToolTip = "Terminate a node into free space with a piston radiation impedance."
    factory = staticmethod(no.make_radiation)


class AddCrossover(_AddToAnalysis):
    Name, object_name, IconName = "AddCrossover", "Crossover", "Crossover"
    MenuText = "Add crossover"
    ToolTip = (
        "Add a filter branch and name the drivers it feeds. Without one, every driver "
        "gets the same voltage at every frequency, which is not a system."
    )
    factory = staticmethod(crossover.make_crossover)

    def run(self) -> None:
        """Create the branch, pre-attaching whatever driver is selected."""
        try:
            import FreeCADGui

            selected = [
                o for o in FreeCADGui.Selection.getSelection()
                if is_audio_object(o, no.Driver.Type)
            ]
        except (ImportError, AttributeError):  # headless: nothing is selected
            selected = []
        super().run()
        if not selected:
            return
        doc = FreeCAD.ActiveDocument
        branch = doc.Objects[-1]
        branch.Drivers = selected
        # Nominal impedance only means anything against a real driver, so seed it.
        branch.NominalImpedance = selected[0].Re
        branch.Proxy.execute(branch)
        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: {branch.Label} feeds "
            f"{', '.join(d.Label for d in selected)}.\n"
        )


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

        from freecad.audio_analysis.checks import check_solution
        from freecad.audio_analysis.results.summary import summarise_solution

        # Some findings only exist once there is an answer -- whether a diaphragm exceeds
        # its linear travel depends on the drive level, not just on the model.
        after = check_solution(solution, analysis)
        if after.diagnostics:
            FreeCAD.Console.PrintMessage(after.format() + "\n")
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


class AddTargetCurve(_AddToAnalysis):
    Name, object_name, IconName = "AddTargetCurve", "TargetCurve", "Target"
    MenuText = "Add target curve"
    ToolTip = (
        "Load a response to aim at, from CSV or FRD. For a headphone this is the whole "
        "argument: there is no room and no reference except an agreed curve."
    )
    factory = staticmethod(study.make_target_curve)

    def run(self) -> None:
        super().run()
        target = FreeCAD.ActiveDocument.Objects[-1]
        path = self.ask_for_file()
        if not path:
            FreeCAD.Console.PrintMessage(
                f"Audio Analysis: {target.Label} added. Set its File property to a CSV or "
                f"FRD target -- none ships with the workbench, because the published ones "
                f"are not redistributable.\n"
            )
            return
        target.File = path
        FreeCAD.Console.PrintMessage(f"Audio Analysis: {target.Label} reads {path}.\n")

    def ask_for_file(self) -> str:
        try:
            from PySide import QtWidgets

            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "Target curve", "", "Curves (*.csv *.frd *.txt);;All files (*)"
            )
            return path
        except Exception:  # noqa: BLE001 -- headless or Qt unavailable
            return ""


class CompareAgainstTarget(AudioCommand):
    """Report how far the most recent solve sits from each loaded target."""

    Name = "CompareAgainstTarget"
    MenuText = "Compare against target"
    ToolTip = (
        "Report RMS deviation from each target curve, over a stated band. The response "
        "is level-matched first, because a target fixes shape rather than loudness."
    )
    IconName = "Target"

    def run(self) -> None:
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.study import LumpedSolver, TargetCurve
        from freecad.audio_analysis.results.target import TargetError

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return

        solvers = [o for o in analysis.Group if is_audio_object(o, LumpedSolver.Type)]
        solution = solvers[0].Proxy.solution if solvers else None
        if solution is None:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no results yet. Solve first.\n"
            )
            return

        targets = [o for o in analysis.Group if is_audio_object(o, TargetCurve.Type)]
        if not targets:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no target curve in this analysis. Add one and point it "
                "at a CSV or FRD file.\n"
            )
            return

        for target in targets:
            try:
                deviation = target.Proxy.compare(target, solution, analysis)
            except TargetError as exc:
                FreeCAD.Console.PrintError(f"Audio Analysis: {target.Label}: {exc}\n")
                continue
            FreeCAD.Console.PrintMessage(deviation.format() + "\n")

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class AddParameterSweep(_AddToAnalysis):
    Name, object_name, IconName = "AddParameterSweep", "ParameterSweep", "Sweep"
    MenuText = "Add parameter sweep"
    ToolTip = (
        "Vary one property across runs and overlay the results. One curve says what a "
        "design does; a family says what a decision does."
    )
    factory = staticmethod(parameter_sweep.make_parameter_sweep)

    def run(self) -> None:
        """Create the sweep, pointing it at whatever is selected."""
        try:
            import FreeCADGui

            selected = FreeCADGui.Selection.getSelection()
        except (ImportError, AttributeError):
            selected = []
        super().run()
        if not selected:
            return
        sweep = FreeCAD.ActiveDocument.Objects[-1]
        sweep.Target = selected[0]
        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: {sweep.Label} targets {selected[0].Label}. Set Property to "
            f"the name of the property to vary, and Values to the values to try.\n"
        )


class RunParameterSweep(AudioCommand):
    """Solve once per swept value and plot the family."""

    Name = "RunParameterSweep"
    MenuText = "Run parameter sweep"
    ToolTip = "Solve the selected sweep across its values and overlay the responses."
    IconName = "Sweep"

    def run(self) -> None:
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.parameter_sweep import ParameterSweep, SweepError
        from freecad.audio_analysis.results.plotting import plot_family

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return

        sweeps = _selected_of_type(ParameterSweep.Type) or [
            o for o in analysis.Group if is_audio_object(o, ParameterSweep.Type)
        ]
        if not sweeps:
            FreeCAD.Console.PrintError(
                "Audio Analysis: no parameter sweep in this analysis. Add one, point it at "
                "an object, and name the property to vary.\n"
            )
            return

        for sweep in sweeps:
            try:
                family = sweep.Proxy.run(sweep, analysis)
            except SweepError as exc:
                FreeCAD.Console.PrintError(f"Audio Analysis: {sweep.Label}: {exc}\n")
                continue
            FreeCAD.Console.PrintMessage(family.summarise() + "\n")
            plot_family(family)

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class ExportResults(AudioCommand):
    """Write the most recent solve to disk.

    Results are recomputed on demand rather than stored in the document (see
    ``objects/study.py``), so export is how a curve outlives the session. CSV for
    inspection and FRD because that is what loudspeaker tools read, which lets a result
    leave for a crossover simulator or an enclosure program.
    """

    Name = "ExportResults"
    MenuText = "Export results"
    ToolTip = (
        "Write the last solve to CSV and FRD files in a chosen folder: one per node "
        "pressure, driver impedance and excursion."
    )
    IconName = "Plot"

    def run(self) -> None:
        from freecad.audio_analysis.objects.base import is_audio_object
        from freecad.audio_analysis.objects.parameter_sweep import ParameterSweep
        from freecad.audio_analysis.objects.study import LumpedSolver

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return

        solvers = [o for o in analysis.Group if is_audio_object(o, LumpedSolver.Type)]
        solution = solvers[0].Proxy.solution if solvers else None
        families = [
            o.Proxy.family for o in analysis.Group
            if is_audio_object(o, ParameterSweep.Type) and o.Proxy.family is not None
        ]
        if solution is None and not families:
            FreeCAD.Console.PrintError(
                "Audio Analysis: nothing to export. Solve first -- results are recomputed "
                "on demand rather than stored in the document.\n"
            )
            return

        directory = self.ask_for_directory()
        if not directory:
            return

        from freecad.audio_analysis.results.export import export_all

        written = export_all(directory, solution, families, analysis)
        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: wrote {len(written)} file(s) to {directory}:\n  "
            + "\n  ".join(written)
            + "\n"
        )

    def ask_for_directory(self) -> str:
        """Prompt for a destination. Returns "" when cancelled or unavailable."""
        try:
            from PySide import QtWidgets

            return QtWidgets.QFileDialog.getExistingDirectory(
                None, "Export audio analysis results"
            )
        except Exception:  # noqa: BLE001 -- headless or Qt unavailable
            return ""

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and find_active_analysis() is not None


class CapOpening(AudioCommand):
    """Build a cap solid across an opening, from one edge on its rim.

    The step that makes cavity extraction usable on a real part. A cup is open at the ear
    side and open again at every port, and until those are closed there is no enclosed
    void to find -- but modelling each plug by hand is tedious enough that the extraction
    command was, in practice, blocked on it.
    """

    Name = "CapOpening"
    MenuText = "Cap opening"
    ToolTip = (
        "Create a cap solid that closes an opening. Pick one edge on the rim and the "
        "rest of the loop is found from it, as fillet does. Capping is not sealing: the "
        "cap closes the fluid domain, and the opening is declared open in the network."
    )
    IconName = "Cap"

    def run(self) -> None:
        import FreeCADGui

        from freecad.audio_analysis.objects import find_active_analysis
        from freecad.audio_analysis.objects.cap_object import make_cap

        references = [
            (selected.Object, tuple(selected.SubElementNames))
            for selected in _unresolved_selection()
            if selected.SubElementNames
        ]
        if not references:
            FreeCAD.Console.PrintError(
                "Audio Analysis: select an edge on the rim of the opening first. A whole "
                "object is not enough -- Cap needs to know which hole to close.\n"
            )
            return

        doc = FreeCAD.ActiveDocument
        with transaction("Cap opening"):
            cap = make_cap(doc, find_active_analysis())
            cap.Opening = references
            cap.Proxy.build(cap)

        if not cap.Shape.isNull():
            FreeCAD.Console.PrintMessage(
                f"Audio Analysis: {cap.Label}:\n{cap.Openings}\n"
                f"Audio Analysis: add {cap.Label} to a cavity's Caps property, then "
                f"extract. If this opening is a port, give its "
                f"{cap.OpeningArea.getValueAs('mm^2').Value:.1f} mm2 to a Port element -- "
                f"capping it here does not make it acoustically closed.\n"
            )

    def IsActive(self) -> bool:
        try:
            return FreeCAD.ActiveDocument is not None and any(
                s.SubElementNames for s in _unresolved_selection()
            )
        except (ImportError, AttributeError):
            return False


class ExtractCavity(AudioCommand):
    """Derive the air from selected parts.

    The step between a mechanical model and an acoustic one: what gets simulated is the
    air, not the parts, and almost nobody models the air directly (STRUCTURE.md §6.5).
    """

    Name = "ExtractCavity"
    MenuText = "Extract cavity from selection"
    ToolTip = (
        "Derive an air volume by subtracting the selected parts from an envelope. "
        "Select the parts that bound the air -- an assembly works. Open models need a "
        "cap solid across the opening before anything is enclosed."
    )
    IconName = "Cavity"

    def run(self) -> None:
        import FreeCADGui

        from freecad.audio_analysis.objects import find_active_analysis
        from freecad.audio_analysis.objects.cavity_object import make_cavity

        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            FreeCAD.Console.PrintError(
                "Audio Analysis: select the parts that bound the air first. An assembly "
                "or a set of solids both work.\n"
            )
            return

        analysis = find_active_analysis()
        doc = FreeCAD.ActiveDocument
        with transaction("Extract cavity"):
            cavity = make_cavity(doc, analysis)
            cavity.Boundary = list(selection)
            cavity.Proxy.extract(cavity)

        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: {cavity.Label} from {len(selection)} object(s):\n"
            f"{cavity.Regions}\n"
        )
        if cavity.Volume.Value > 0:
            FreeCAD.Console.PrintMessage(
                f"Audio Analysis: kept {cavity.Volume.getValueAs('cm^3').Value:.3f} cm3. "
                f"Link it from an acoustic volume, or use 'Volume from cavity'.\n"
            )

    def IsActive(self) -> bool:
        try:
            import FreeCADGui

            return FreeCAD.ActiveDocument is not None and bool(
                FreeCADGui.Selection.getSelection()
            )
        except ImportError:
            return False


class VolumeFromCavity(AudioCommand):
    """Create an AcousticVolume that reads its volume from a selected cavity."""

    Name = "VolumeFromCavity"
    MenuText = "Volume from cavity"
    ToolTip = (
        "Create an acoustic volume linked to the selected cavity, so its value follows "
        "the geometry instead of being typed."
    )
    IconName = "Volume"

    def run(self) -> None:
        import FreeCADGui

        from freecad.audio_analysis.objects import find_active_analysis, network_objects as no

        analysis = find_active_analysis()
        if analysis is None:
            FreeCAD.Console.PrintError("Audio Analysis: no active analysis.\n")
            return

        selection = FreeCADGui.Selection.getSelection()
        sources = [o for o in selection if getattr(o, "Shape", None) is not None]
        if not sources:
            FreeCAD.Console.PrintError(
                "Audio Analysis: select a cavity, or any solid representing the air.\n"
            )
            return

        with transaction("Volume from cavity"):
            for source in sources:
                volume = no.make_volume(FreeCAD.ActiveDocument, analysis, "Volume")
                volume.Shape = source
                volume.Label = f"{source.Label} air"
                volume.Proxy.execute(volume)
                FreeCAD.Console.PrintMessage(
                    f"Audio Analysis: {volume.Label} = "
                    f"{volume.Volume.getValueAs('cm^3').Value:.3f} cm3 from {source.Label}.\n"
                )

    def IsActive(self) -> bool:
        try:
            import FreeCADGui

            return (
                FreeCAD.ActiveDocument is not None
                and find_active_analysis() is not None
                and bool(FreeCADGui.Selection.getSelection())
            )
        except ImportError:
            return False


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


GEOMETRY_COMMANDS = (CapOpening, ExtractCavity, VolumeFromCavity)
MODEL_COMMANDS = (
    AddVolume, AddNode, AddDriver, AddCrossover, AddPort, AddResistance, AddLeak,
    AddPassiveRadiator, AddRadiation,
)
SOLVE_COMMANDS = (
    AddFrequencySweep, AddLumpedSolver, RunLumpedSolver, PlotResults,
    AddParameterSweep, RunParameterSweep,
    AddTargetCurve, CompareAgainstTarget, ExportResults,
)
TEMPLATE_COMMANDS = (NewFromTemplate,)


def register_all() -> tuple[list[str], list[str], list[str], list[str]]:
    """Register each group, returning their command names."""
    templates = [register(cls()) for cls in TEMPLATE_COMMANDS]
    geometry = [register(cls()) for cls in GEOMETRY_COMMANDS]
    model = [register(cls()) for cls in MODEL_COMMANDS]
    solve = [register(cls()) for cls in SOLVE_COMMANDS]
    return templates, geometry, model, solve
