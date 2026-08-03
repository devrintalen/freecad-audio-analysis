"""The crossover as a document object: properties, the builder seam, and the checks.

The physics is covered in ``test_crossover.py``. What is tested here is that clicking a
crossover together in FreeCAD produces the filter the user asked for, in the right units,
and that the checks catch the mistakes a crossover invites -- above all the polarity trap,
which is silent, severe, and impossible to see in a parts list.
"""

from __future__ import annotations

import numpy as np
import pytest

FreeCAD = pytest.importorskip("FreeCAD")

from freecad.audio_analysis.builder import build_network, filter_for  # noqa: E402
from freecad.audio_analysis.checks import run_checks  # noqa: E402
from freecad.audio_analysis.objects import (  # noqa: E402
    make_analysis,
    make_environment,
    network_objects as no,
    study,
)
from freecad.audio_analysis.objects.crossover import (  # noqa: E402
    CrossoverFilter,
    crossover_for,
    make_crossover,
)
from freecad.audio_analysis.physics.crossover import IdealFilter, PassiveLadder  # noqa: E402
from freecad.audio_analysis.templates import apply_template  # noqa: E402


def q(text: str):
    return FreeCAD.Units.Quantity(text)


@pytest.fixture
def doc():
    document = FreeCAD.newDocument("crossover_test")
    name = document.Name
    yield document
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


@pytest.fixture
def two_way(doc):
    """A woofer and a tweeter into one shared ear cavity, no crossover yet."""
    analysis = make_analysis(doc)
    make_environment(doc, analysis)
    ear = no.make_volume(doc, analysis, "EarCavity")
    ear.Volume = q("100 cm^3")
    cup = no.make_volume(doc, analysis, "CupCavity")
    cup.Volume = q("200 cm^3")

    woofer = no.make_driver(doc, analysis, "Woofer")
    woofer.FrontNode, woofer.BackNode = ear, cup
    tweeter = no.make_driver(doc, analysis, "Tweeter")
    tweeter.FrontNode, tweeter.BackNode = ear, cup
    tweeter.Fs, tweeter.Sd, tweeter.Vas = q("1200 Hz"), q("3 cm^2"), q("0.02 l")

    leak = no.make_leak(doc, analysis, "PadLeak")
    leak.NodeA = ear

    solver = study.make_lumped_solver(doc, analysis)
    solver.LargestDimension = q("105 mm")
    doc.recompute()
    return analysis


def drivers_of(analysis):
    from freecad.audio_analysis.objects.base import is_audio_object

    return [o for o in analysis.Group if is_audio_object(o, no.Driver.Type)]


# ---------------------------------------------------------------------------------
# Properties and units
# ---------------------------------------------------------------------------------


class TestProperties:
    def test_a_new_crossover_is_a_bypass(self, doc):
        """The default must change nothing, so adding one is never a silent surprise."""
        branch = make_crossover(doc)
        assert str(branch.Response) == "Bypass"
        filter_ = branch.Proxy.filter(branch)
        omega = np.array([100.0, 10000.0])
        gain, alpha, beta = filter_.terminal_coefficients(omega, 0.0)
        assert np.allclose(gain, 1.0)
        assert np.allclose(alpha, 1.0)
        assert np.allclose(beta, 0.0)

    def test_frequency_is_read_in_hertz(self, doc):
        branch = make_crossover(doc)
        branch.Response, branch.Frequency = "Lowpass", q("2.5 kHz")
        assert branch.Proxy.filter(branch).frequency == pytest.approx(2500.0)

    def test_delay_is_read_in_seconds(self, doc):
        """FreeCAD's internal time unit is seconds, but nothing here relies on that."""
        branch = make_crossover(doc)
        branch.Delay = q("0.00025 s")
        assert branch.Proxy.filter(branch).delay == pytest.approx(250e-6)

    def test_realisation_selects_the_filter_kind(self, doc):
        branch = make_crossover(doc)
        branch.Response, branch.Order = "Lowpass", 2
        assert isinstance(branch.Proxy.filter(branch), IdealFilter)
        branch.Realisation = "Passive"
        assert isinstance(branch.Proxy.filter(branch), PassiveLadder)

    def test_components_are_derived_not_typed(self, doc):
        """The parts list must follow the crossover frequency, not be entered beside it."""
        branch = make_crossover(doc)
        branch.Response, branch.Order = "Lowpass", 2
        branch.Realisation, branch.NominalImpedance = "Passive", 8.0
        branch.Frequency = q("1000 Hz")
        branch.Proxy.execute(branch)
        first = branch.Components

        branch.Frequency = q("2000 Hz")
        branch.Proxy.execute(branch)
        assert branch.Components != first
        assert "mH" in branch.Components and "uF" in branch.Components

    def test_components_report_an_unrealisable_combination(self, doc):
        branch = make_crossover(doc)
        branch.Response, branch.Alignment, branch.Order = "Lowpass", "Linkwitz-Riley", 3
        branch.Proxy.execute(branch)
        assert "unrealisable" in branch.Components

    def test_properties_survive_a_reload(self, doc, tmp_path):
        branch = make_crossover(doc)
        branch.Response, branch.Order, branch.Gain = "Highpass", 4, -3.5
        branch.Frequency = q("1800 Hz")
        path = str(tmp_path / "crossover.FCStd")
        doc.saveAs(path)
        name = doc.Name
        FreeCAD.closeDocument(name)

        reopened = FreeCAD.openDocument(path)
        try:
            restored = reopened.Objects[0]
            assert str(restored.Response) == "Highpass"
            assert restored.Order == 4
            assert restored.Gain == pytest.approx(-3.5)
            assert restored.Frequency.getValueAs("Hz").Value == pytest.approx(1800.0)
        finally:
            FreeCAD.closeDocument(reopened.Name)


# ---------------------------------------------------------------------------------
# The builder seam
# ---------------------------------------------------------------------------------


class TestBuilder:
    def test_a_driver_with_no_crossover_gets_none(self, two_way):
        assert filter_for(two_way, drivers_of(two_way)[0]) is None

    def test_crossover_for_finds_the_owning_branch(self, doc, two_way):
        woofer, tweeter = drivers_of(two_way)
        branch = make_crossover(doc, two_way, "LowPass")
        branch.Drivers = [woofer]
        assert crossover_for(two_way, woofer) is branch
        assert crossover_for(two_way, tweeter) is None

    def test_the_filter_reaches_the_solved_network(self, doc, two_way):
        woofer, tweeter = drivers_of(two_way)
        low = make_crossover(doc, two_way, "LowPass")
        low.Drivers, low.Response, low.Order = [woofer], "Lowpass", 4
        low.Frequency = q("2500 Hz")
        doc.recompute()

        network, _ = build_network(two_way)
        assert network.element(woofer.Name).filter is not None
        assert network.element(tweeter.Name).filter is None

    def test_a_crossover_actually_splits_the_band(self, doc, two_way):
        """The whole point: each driver contributes where its filter lets it."""
        woofer, tweeter = drivers_of(two_way)
        low = make_crossover(doc, two_way, "LowPass")
        low.Drivers, low.Response, low.Order = [woofer], "Lowpass", 4
        low.Frequency = q("2500 Hz")
        high = make_crossover(doc, two_way, "HighPass")
        high.Drivers, high.Response, high.Order = [tweeter], "Highpass", 4
        high.Frequency = q("2500 Hz")
        doc.recompute()

        network, _ = build_network(two_way)
        frequency = np.array([100.0, 15000.0])
        solution = network.solve(frequency)
        woofer_flow = np.abs(solution.volume_velocity(woofer.Name).values)
        tweeter_flow = np.abs(solution.volume_velocity(tweeter.Name).values)

        assert woofer_flow[0] > tweeter_flow[0]  # bass belongs to the woofer
        assert tweeter_flow[1] > woofer_flow[1]  # treble to the tweeter

    def test_an_unrealisable_crossover_is_a_build_error(self, doc, two_way):
        from freecad.audio_analysis.builder import BuildError

        woofer = drivers_of(two_way)[0]
        branch = make_crossover(doc, two_way, "Bad")
        branch.Drivers, branch.Response = [woofer], "Lowpass"
        branch.Alignment, branch.Order = "Linkwitz-Riley", 3
        with pytest.raises(BuildError, match="even orders"):
            build_network(two_way)

    def test_gain_moves_the_drive_voltage_by_the_amount_asked(self, doc, two_way):
        woofer, tweeter = drivers_of(two_way)
        branch = make_crossover(doc, two_way, "Pad")
        branch.Drivers, branch.Gain = [tweeter], -6.0
        doc.recompute()

        network, _ = build_network(two_way)
        omega = np.array([2.0 * np.pi * 300.0])
        padded = network.element(tweeter.Name).coefficients(omega)[0]
        direct = network.element(woofer.Name).coefficients(omega)[0]
        assert 20.0 * np.log10(np.abs(padded / direct))[0] == pytest.approx(-6.0, abs=1e-9)

    def test_padding_a_shared_driver_moves_its_output_by_less_than_the_pad(self, doc, two_way):
        """Because the two drivers share the ear cavity.

        Attenuating the tweeter by 6 dB does not drop its volume velocity by 6 dB: the
        pressure it works against is set mostly by the woofer and barely changes, so the
        tweeter's flow does not scale with its drive. A single-driver model would predict
        exactly 6 dB and be wrong (STRUCTURE.md §2.4).
        """
        _, tweeter = drivers_of(two_way)
        frequency = np.array([300.0])

        network, _ = build_network(two_way)
        before = np.abs(network.solve(frequency).volume_velocity(tweeter.Name).values)

        branch = make_crossover(doc, two_way, "Pad")
        branch.Drivers, branch.Gain = [tweeter], -6.0
        doc.recompute()
        network, _ = build_network(two_way)
        after = np.abs(network.solve(frequency).volume_velocity(tweeter.Name).values)

        moved = 20.0 * np.log10(after / before)[0]
        assert -6.0 < moved < -1.0


# ---------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------


def codes(analysis) -> set[str]:
    return {d.code for d in run_checks(analysis).diagnostics}


class TestChecks:
    def test_an_unattached_crossover_is_flagged(self, doc, two_way):
        make_crossover(doc, two_way, "Orphan")
        doc.recompute()
        assert "crossover-unattached" in codes(two_way)

    def test_two_crossovers_on_one_driver_block_the_solve(self, doc, two_way):
        woofer = drivers_of(two_way)[0]
        for name in ("First", "Second"):
            branch = make_crossover(doc, two_way, name)
            branch.Drivers = [woofer]
        doc.recompute()
        report = run_checks(two_way)
        assert not report.can_solve
        assert "crossover-conflict" in {d.code for d in report.errors}

    def test_a_passive_branch_cannot_amplify(self, doc, two_way):
        branch = make_crossover(doc, two_way, "Pad")
        branch.Drivers, branch.Realisation, branch.Gain = drivers_of(two_way)[:1], "Passive", 3.0
        doc.recompute()
        assert "passive-cannot-amplify" in codes(two_way)

    def test_a_passive_branch_cannot_delay(self, doc, two_way):
        branch = make_crossover(doc, two_way, "Delayed")
        branch.Drivers, branch.Realisation = drivers_of(two_way)[:1], "Passive"
        branch.Delay = q("0.0005 s")
        doc.recompute()
        assert "passive-cannot-delay" in codes(two_way)

    def test_an_active_branch_may_delay_and_amplify(self, doc, two_way):
        branch = make_crossover(doc, two_way, "Active")
        branch.Drivers, branch.Gain = drivers_of(two_way)[:1], 3.0
        branch.Delay = q("0.0005 s")
        doc.recompute()
        found = codes(two_way)
        assert "passive-cannot-amplify" not in found
        assert "passive-cannot-delay" not in found

    def test_nominal_impedance_far_from_the_driver_is_flagged(self, doc, two_way):
        branch = make_crossover(doc, two_way, "LowPass")
        branch.Drivers, branch.Response = drivers_of(two_way)[:1], "Lowpass"
        branch.Realisation, branch.NominalImpedance = "Passive", 4.0  # driver Re is 32
        doc.recompute()
        assert "crossover-impedance-mismatch" in codes(two_way)

    def test_a_matched_nominal_impedance_is_quiet(self, doc, two_way):
        woofer = drivers_of(two_way)[0]
        branch = make_crossover(doc, two_way, "LowPass")
        branch.Drivers, branch.Response = [woofer], "Lowpass"
        branch.Realisation, branch.NominalImpedance = "Passive", woofer.Re
        doc.recompute()
        assert "crossover-impedance-mismatch" not in codes(two_way)


class TestPolarity:
    """The trap: an even-order crossover rotates phase, and at some orders the drivers
    cancel unless one is wired backwards. Nothing in a parts list shows this."""

    def build_pair(self, doc, analysis, order: int):
        woofer, tweeter = drivers_of(analysis)
        low = make_crossover(doc, analysis, "LowPass")
        low.Drivers, low.Response, low.Order = [woofer], "Lowpass", order
        low.Frequency = q("2500 Hz")
        high = make_crossover(doc, analysis, "HighPass")
        high.Drivers, high.Response, high.Order = [tweeter], "Highpass", order
        high.Frequency = q("2500 Hz")
        doc.recompute()
        return woofer, tweeter

    def test_lr2_without_inversion_is_flagged(self, doc, two_way):
        self.build_pair(doc, two_way, 2)
        assert "crossover-polarity" in codes(two_way)

    def test_lr2_with_inversion_is_quiet(self, doc, two_way):
        _, tweeter = self.build_pair(doc, two_way, 2)
        tweeter.Inverted = True
        doc.recompute()
        assert "crossover-polarity" not in codes(two_way)

    def test_lr4_without_inversion_is_quiet(self, doc, two_way):
        self.build_pair(doc, two_way, 4)
        assert "crossover-polarity" not in codes(two_way)

    def test_lr4_with_inversion_is_flagged(self, doc, two_way):
        _, tweeter = self.build_pair(doc, two_way, 4)
        tweeter.Inverted = True
        doc.recompute()
        assert "crossover-polarity" in codes(two_way)

    def test_the_flagged_case_really_does_cancel(self, doc):
        """The check is only worth having if the physics behind it is real.

        Two *identical* drivers, so the branches are matched in level as well as opposed
        in phase and the null is unambiguous. With a mismatched pair the cancellation is
        partial, which is exactly why this is worth warning about rather than leaving
        someone to hear a dip and blame the drivers.
        """
        analysis = make_analysis(doc)
        make_environment(doc, analysis)
        ear = no.make_volume(doc, analysis, "EarCavity")
        ear.Volume = q("100 cm^3")
        pair = []
        for name in ("A", "B"):
            driver = no.make_driver(doc, analysis, name)
            driver.FrontNode = ear
            pair.append(driver)
        leak = no.make_leak(doc, analysis, "PadLeak")
        leak.NodeA = ear

        low = make_crossover(doc, analysis, "LowPass")
        low.Drivers, low.Response, low.Order = [pair[0]], "Lowpass", 2
        low.Frequency = q("2500 Hz")
        high = make_crossover(doc, analysis, "HighPass")
        high.Drivers, high.Response, high.Order = [pair[1]], "Highpass", 2
        high.Frequency = q("2500 Hz")
        doc.recompute()

        frequency = np.array([2500.0])
        network, _ = build_network(analysis)
        in_phase = network.solve(frequency).pressure(ear.Name).spl[0]

        pair[1].Inverted = True
        doc.recompute()
        network, _ = build_network(analysis)
        inverted = network.solve(frequency).pressure(ear.Name).spl[0]

        assert inverted - in_phase > 20.0

    def test_butterworth_pairs_are_not_flagged(self, doc, two_way):
        """Odd-order Butterworth sums all-pass, so there is no single right polarity and
        the check should keep quiet rather than guess."""
        woofer, tweeter = drivers_of(two_way)
        for name, response, driver in (
            ("LowPass", "Lowpass", woofer), ("HighPass", "Highpass", tweeter)
        ):
            branch = make_crossover(doc, two_way, name)
            branch.Drivers, branch.Response = [driver], response
            branch.Alignment, branch.Order = "Butterworth", 3
            branch.Frequency = q("2500 Hz")
        doc.recompute()
        assert "crossover-polarity" not in codes(two_way)


class TestValidityWarning:
    def test_a_crossover_above_the_lumped_limit_is_flagged(self, doc, two_way):
        """A 105 mm cup is lumped-valid to about 400 Hz; crossovers live far above that."""
        branch = make_crossover(doc, two_way, "LowPass")
        branch.Drivers, branch.Response = drivers_of(two_way)[:1], "Lowpass"
        branch.Frequency = q("2500 Hz")
        doc.recompute()
        assert "crossover-beyond-validity" in codes(two_way)

    def test_a_crossover_below_the_limit_is_not(self, doc, two_way):
        branch = make_crossover(doc, two_way, "LowPass")
        branch.Drivers, branch.Response = drivers_of(two_way)[:1], "Lowpass"
        branch.Frequency = q("150 Hz")
        doc.recompute()
        assert "crossover-beyond-validity" not in codes(two_way)

    def test_a_bypass_branch_has_no_crossover_frequency_to_check(self, doc, two_way):
        branch = make_crossover(doc, two_way, "Pad")
        branch.Drivers, branch.Gain = drivers_of(two_way)[:1], -3.0
        branch.Frequency = q("18000 Hz")
        doc.recompute()
        assert "crossover-beyond-validity" not in codes(two_way)


# ---------------------------------------------------------------------------------
# The two-way template
# ---------------------------------------------------------------------------------


class TestTwoWayTemplate:
    @pytest.fixture
    def analysis(self, doc):
        from freecad.audio_analysis.objects import make_environment

        built = make_analysis(doc)
        make_environment(doc, built)
        apply_template("over_ear_two_way", doc, built)
        return built

    def test_both_drivers_share_the_ear_cavity(self, analysis):
        network, _ = build_network(analysis)
        fronts = {d.front_node for d in network.drivers}
        assert len(network.drivers) == 2
        assert len(fronts) == 1, "a two-way over-ear radiates into one ear cavity"

    def test_the_tweeter_has_its_own_back_chamber(self, analysis):
        network, _ = build_network(analysis)
        backs = {d.name: d.back_node for d in network.drivers}
        assert len(set(backs.values())) == 2

    def test_the_drivers_load_each_other(self, analysis, doc):
        """Superposing two independent single-driver runs would not give this answer.

        Deleting the tweeter changes the woofer's own output, because the tweeter's
        diaphragm is part of what the woofer pushes against.
        """
        from freecad.audio_analysis.objects.base import is_audio_object

        frequency = np.array([2500.0])
        network, _ = build_network(analysis)
        woofer_name = next(
            o.Name for o in analysis.Group
            if is_audio_object(o, no.Driver.Type) and o.Label == "Woofer"
        )
        together = network.solve(frequency).volume_velocity(woofer_name).values[0]

        # The tweeter's sealed chamber and its crossover branch go with it; a chamber left
        # behind would be a node with one connection, which is a wiring error in itself.
        for label in ("Tweeter", "TweeterChamber", "HighPass"):
            victim = next(o for o in analysis.Group if o.Label == label)
            analysis.removeObject(victim)
            doc.removeObject(victim.Name)
        doc.recompute()
        network, _ = build_network(analysis)
        alone = network.solve(frequency).volume_velocity(woofer_name).values[0]

        assert abs(together - alone) / abs(alone) > 1e-3

    def test_it_solves_across_the_whole_band(self, analysis):
        from freecad.audio_analysis.builder import sweep_frequencies

        network, _ = build_network(analysis)
        solution = network.solve(sweep_frequencies(analysis))
        assert np.all(np.isfinite(solution.pressure("EarCavity").spl))

    def test_the_system_impedance_is_the_two_branches_in_parallel(self, analysis):
        network, _ = build_network(analysis)
        solution = network.solve(np.logspace(1.3, 4.3, 100))
        names = [d.name for d in network.drivers]
        system = solution.system_impedance().values
        expected = 1.0 / sum(1.0 / solution.input_impedance(n).values for n in names)
        assert np.allclose(system, expected)
