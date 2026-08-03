"""Per-element validity limits.

The point of this module is attribution: not "the model expires at 407 Hz" but "the cup
expires at 407 Hz and everything else in the model is good to 740 Hz or beyond". So the
tests check that each element answers with the dimension that actually governs it, that
the binding element is identified, and — the one most likely to mislead if it were wrong
— that a limit resting on a guess about shape is labelled as such.
"""

from __future__ import annotations

import math

import pytest

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.physics.network import (
    AcousticMass,
    Compliance,
    Driver,
    Leak,
    Network,
    PassiveRadiator,
    PistonRadiation,
    Resistance,
)
from freecad.audio_analysis.physics.validity import (
    CONFIDENT_FRACTION,
    LIMIT_FRACTION,
    ValidityReport,
    assess,
)

MEDIUM = air.AirProperties.at()
C = MEDIUM.speed_of_sound


def limit_for(length_mm: float) -> float:
    return C / (8.0 * length_mm / 1000.0)


class TestCharacteristicLengths:
    def test_a_measured_cavity_uses_its_widest_span(self):
        element = Compliance("cup", 200e-6, "Cup", largest_dimension=0.1056)
        length, basis = element.characteristic_length()
        assert length == pytest.approx(0.1056)
        assert "measured" in basis

    def test_an_unmeasured_cavity_falls_back_to_an_equivalent_sphere(self):
        element = Compliance("cup", 200e-6, "Cup")
        length, basis = element.characteristic_length()
        assert length == pytest.approx((6.0 * 200e-6 / math.pi) ** (1 / 3))
        assert "optimistic" in basis

    def test_the_fallback_is_optimistic_by_a_large_margin(self):
        """The number that justifies warning about it.

        A sphere is the most compact body of a given volume, so guessing from volume gives
        the highest limit the cavity could possibly have. For a real headphone cup that is
        a 46% overstatement in the direction that flatters the model.
        """
        guessed = assess([Compliance("cup", 200e-6, "Cup")], MEDIUM).limit
        measured = assess(
            [Compliance("cup", 200e-6, "Cup", largest_dimension=0.1056)], MEDIUM
        ).limit
        assert guessed / measured == pytest.approx(1.46, abs=0.02)

    def test_a_port_uses_its_effective_length_or_its_mouth(self):
        long_duct = AcousticMass("port", area=4e-4, length=0.12, node_a="Box")
        assert long_duct.characteristic_length()[0] == pytest.approx(
            long_duct.effective_length
        )
        # A short wide vent is governed by its mouth instead.
        wide = AcousticMass("vent", area=8e-4, length=0.003, node_a="Cup")
        assert wide.characteristic_length()[0] == pytest.approx(2.0 * wide.radius)

    def test_a_leak_uses_its_depth_not_its_perimeter(self):
        """An earpad's leak is 350 mm around and 4 mm deep.

        Taking the perimeter would put its limit at 120 Hz and condemn every headphone
        model ever built; it is the path the air travels through that must be short.
        """
        element = Leak("seal", gap=0.15e-3, width=0.35, length=0.004, node_a="Ear")
        length, basis = element.characteristic_length()
        assert length == pytest.approx(0.004)
        assert "depth" in basis

    def test_a_mesh_uses_the_aperture_it_covers(self):
        element = Resistance.from_rayls("mesh", 20.0, 8e-4, "Behind")
        assert element.characteristic_length()[0] == pytest.approx(
            math.sqrt(4.0 * 8e-4 / math.pi)
        )

    def test_a_mesh_given_only_a_resistance_has_no_dimension_to_go_on(self):
        assert Resistance("mesh", 1000.0, "Behind").characteristic_length()[0] is None

    def test_a_driver_uses_its_diaphragm_diameter(self):
        parameters = DriverParameters.from_thiele_small(
            name="d", fs=45.0, Re=32.0, Qms=2.5, Qes=0.7, Sd=26.4e-4, Vas=2.5e-3
        )
        element = Driver("D", parameters, front_node="Ear")
        length, basis = element.characteristic_length()
        assert length == pytest.approx(2.0 * math.sqrt(26.4e-4 / math.pi))
        assert "diaphragm" in basis

    def test_a_passive_radiator_does_too(self):
        element = PassiveRadiator(
            "PR", mass=0.02, compliance=5e-4, area=20e-4, node_a="Box"
        )
        assert element.characteristic_length()[0] == pytest.approx(
            2.0 * math.sqrt(20e-4 / math.pi)
        )

    def test_radiation_imposes_no_limit_because_it_is_exact(self):
        """The Bessel/Struve expression is the exact baffled-piston result at every ka."""
        assert PistonRadiation("R", 26.4e-4, "Ear").characteristic_length()[0] is None


class TestReport:
    def headphone(self) -> Network:
        parameters = DriverParameters.from_thiele_small(
            name="d", fs=45.0, Re=32.0, Qms=2.5, Qes=0.7, Sd=26.4e-4, Vas=2.5e-3
        )
        network = Network(MEDIUM)
        network.add(Driver("Woofer", parameters, front_node="Ear", back_node="Cup"))
        network.add(Compliance("EarCavity", 100e-6, "Ear", largest_dimension=0.090))
        network.add(Compliance("CupCavity", 200e-6, "Cup", largest_dimension=0.1056))
        network.add(Leak("PadSeal", gap=0.15e-3, width=0.35, length=0.004, node_a="Ear"))
        network.add(AcousticMass("RearVent", area=8e-4, length=0.003, node_a="Cup",
                                 node_b="Behind"))
        network.add(Resistance.from_rayls("VentMesh", 20.0, 8e-4, "Behind"))
        return network

    def test_the_cup_is_the_binding_element(self):
        report = self.headphone().validity()
        assert report.binding.name == "CupCavity"
        assert report.limit == pytest.approx(limit_for(105.6), rel=1e-6)

    def test_elements_are_listed_worst_first(self):
        names = [item.name for item in self.headphone().validity().bounded]
        assert names[0] == "CupCavity"
        assert names[-1] == "PadSeal"

    def test_the_leak_is_valid_two_decades_higher_than_the_cup(self):
        """The attribution that matters: the model of the thing that dominates measured
        bass was never the weak link."""
        report = self.headphone().validity()
        leak = next(i for i in report.bounded if i.name == "PadSeal")
        assert leak.limit > 10_000.0
        assert leak.limit / report.limit > 20.0

    def test_the_confident_threshold_is_half_the_limit(self):
        """lambda/16 against lambda/8, exactly."""
        report = self.headphone().validity()
        assert report.confident_below == pytest.approx(report.limit / 2.0)
        assert CONFIDENT_FRACTION == pytest.approx(LIMIT_FRACTION / 2.0)

    def test_headroom_says_how_much_one_element_costs(self):
        report = self.headphone().validity()
        assert report.headroom == pytest.approx(
            report.bounded[1].limit / report.limit
        )
        assert report.headroom > 1.0

    def test_the_report_names_the_binding_element(self):
        text = self.headphone().validity().format()
        assert "set by CupCavity" in text
        assert "PadSeal" in text

    def test_labels_replace_internal_names(self):
        text = self.headphone().validity().format({"CupCavity": "Left cup interior"})
        assert "Left cup interior" in text
        assert "set by Left cup interior" in text

    def test_assumed_dimensions_are_reported_separately(self):
        network = self.headphone()
        network.add(Compliance("Guessed", 50e-6, "Extra", "Cup"))
        assumed = [i.name for i in network.validity().uses_assumed_dimensions()]
        assert assumed == ["Guessed"]

    def test_a_network_of_exact_elements_has_no_limit(self):
        report = assess([PistonRadiation("R", 26.4e-4, "Ear")], MEDIUM)
        assert report.limit is None
        assert report.binding is None
        assert "No element imposes" in report.format()

    def test_an_empty_report_says_so(self):
        assert "Nothing in this model" in ValidityReport([]).format()


class TestPropagation:
    def test_a_solve_carries_the_limit_without_being_asked(self):
        """CLAUDE.md requires every lumped result to state where it stops being true, so
        the default must supply one rather than leaving it unstated."""
        import numpy as np

        network = TestReport().headphone()
        solution = network.solve(np.logspace(1, 4, 50))
        assert solution.valid_below == pytest.approx(limit_for(105.6), rel=1e-6)
        assert solution.limited_by == "CupCavity"

    def test_an_explicit_limit_still_wins(self):
        import numpy as np

        solution = TestReport().headphone().solve(np.logspace(1, 4, 50), valid_below=1000.0)
        assert solution.valid_below == 1000.0

    def test_curves_name_the_binding_element_in_their_metadata(self):
        import numpy as np

        solution = TestReport().headphone().solve(np.logspace(1, 4, 50))
        assert solution.pressure("Ear").metadata["limited by"] == "CupCavity"

    def test_the_plot_shades_two_bands_and_names_the_culprit(self):
        import numpy as np

        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("Agg")
        from freecad.audio_analysis.results.plotting import plot_curves

        solution = TestReport().headphone().solve(np.logspace(1, 4.3, 200))
        axis = plot_curves([solution.pressure("Ear")])
        assert len(axis.patches) == 2, "a degrading band and an invalid band"
        labels = [text.get_text() for text in axis.texts]
        assert any("CupCavity" in text for text in labels), labels
        assert any("under 0.5 dB below 204 Hz" in text for text in labels), labels
