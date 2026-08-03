"""The command layer.

Commands are the only part of the workbench that touches ``FreeCADGui``, so they are the
part least covered by everything else. What is checked here is what breaks silently in a
GUI: a command whose icon file does not exist gets a blank toolbar button, and a command
missing from a toolbar group is simply unreachable.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

FreeCAD = pytest.importorskip("FreeCAD")
pytest.importorskip("FreeCADGui")

from freecad.audio_analysis.commands import (  # noqa: E402
    analysis_commands,
    measure_volume,
    network_commands,
)


def all_command_classes():
    """Every command the workbench puts on a toolbar."""
    return [
        *network_commands.TEMPLATE_COMMANDS,
        *network_commands.GEOMETRY_COMMANDS,
        *network_commands.MODEL_COMMANDS,
        *network_commands.SOLVE_COMMANDS,
        analysis_commands.NewAnalysis,
        analysis_commands.AddEnvironment,
        analysis_commands.CheckSetup,
        analysis_commands.SolverStatus,
        measure_volume.MeasureVolume,
    ]


@pytest.mark.parametrize("command", all_command_classes(), ids=lambda c: c.Name)
class TestEveryCommand:
    def test_has_an_icon_file(self, command):
        from freecad.audio_analysis import icon

        path = icon(command.IconName)
        assert os.path.exists(path), f"{command.Name} points at a missing icon: {path}"

    def test_has_menu_text_and_a_tooltip(self, command):
        assert command.MenuText, f"{command.Name} would appear as a blank menu entry"
        assert len(command.ToolTip) > 20, f"{command.Name} needs a tooltip worth reading"

    def test_has_a_unique_name(self, command):
        names = [c.Name for c in all_command_classes()]
        assert names.count(command.Name) == 1


def test_every_object_factory_is_reachable_from_a_command():
    """An object with no command exists only to Python users, which is not the point."""
    from freecad.audio_analysis.objects import network_objects as no

    reachable = {c.factory for c in network_commands.MODEL_COMMANDS if hasattr(c, "factory")}
    expected = {
        no.make_volume, no.make_node, no.make_driver, no.make_port,
        no.make_resistance, no.make_leak, no.make_radiation, no.make_passive_radiator,
    }
    missing = {f.__name__ for f in expected if f not in reachable}
    assert not missing, f"no command creates: {sorted(missing)}"


class TestPostSolveChecks:
    @pytest.fixture
    def solution(self):
        from freecad.audio_analysis.physics import air
        from freecad.audio_analysis.physics.driver import DriverParameters
        from freecad.audio_analysis.physics.network import Compliance, Driver, Network

        parameters = DriverParameters.from_thiele_small(
            name="d", fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=133e-4, Vas=10e-3, Xmax=0.5e-3
        )
        network = Network(air.AirProperties.at())
        network.add(Driver("D", parameters, front_node="Front", voltage=20.0))
        network.add(Compliance("front", 1.0, "Front"))
        return network.solve(np.logspace(1, 3, 200), valid_below=400.0)

    def test_excursion_beyond_xmax_is_reported(self, solution):
        from freecad.audio_analysis.checks import check_solution

        report = check_solution(solution)
        assert "excursion-exceeds-xmax" in {d.code for d in report.diagnostics}

    def test_it_says_the_model_cannot_represent_the_distortion(self, solution):
        """The remedy has to explain that the curve stays clean regardless."""
        from freecad.audio_analysis.checks import check_solution

        finding = check_solution(solution).diagnostics[0]
        assert "small-signal" in finding.why
        assert finding.remedy

    def test_a_quiet_drive_produces_no_finding(self):
        from freecad.audio_analysis.checks import check_solution
        from freecad.audio_analysis.physics import air
        from freecad.audio_analysis.physics.driver import DriverParameters
        from freecad.audio_analysis.physics.network import Compliance, Driver, Network

        parameters = DriverParameters.from_thiele_small(
            name="d", fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=133e-4, Vas=10e-3, Xmax=5e-3
        )
        network = Network(air.AirProperties.at())
        network.add(Driver("D", parameters, front_node="Front", voltage=0.1))
        network.add(Compliance("front", 1.0, "Front"))
        solution = network.solve(np.logspace(1, 3, 200), valid_below=400.0)
        assert check_solution(solution).diagnostics == []

    def test_only_the_trusted_range_is_examined(self, solution):
        """A peak above the validity limit is not evidence of anything."""
        from freecad.audio_analysis.checks import check_solution

        finding = check_solution(solution).diagnostics[0]
        frequency = float(finding.message.split(" at ")[1].split(" Hz")[0])
        assert frequency <= 400.0
