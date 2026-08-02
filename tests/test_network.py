"""Lumped network solver validation.

The important tests here compare the solver against **closed-form theory computed
independently**, not against previously recorded solver output. A sealed box has an exact
analytic resonance and an exact second-order response shape; if the network solver
reproduces them from first principles, the assembly, the analogy and the driver model are
all working together.

The two-driver tests exist because superposition is the tempting shortcut and it is wrong
(STRUCTURE.md §2.4).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.physics.network import (
    GROUND,
    AcousticMass,
    Compliance,
    Driver,
    Leak,
    Network,
    PassiveRadiator,
    PistonRadiation,
    Resistance,
)
from freecad.audio_analysis.results.curve import log_frequencies

MEDIUM = air.AirProperties.at()


def woofer(**overrides) -> DriverParameters:
    """A plausible small woofer, in the spirit of a 70 mm headphone driver's big cousin."""
    defaults = dict(
        name="woofer", fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=0.0133, Vas=0.010, Xmax=0.005
    )
    defaults.update(overrides)
    return DriverParameters.from_thiele_small(medium=MEDIUM, **defaults)


def resonance_from_impedance(solution, driver_name: str) -> float:
    """Frequency of the electrical impedance peak -- how resonance is measured in reality."""
    z = solution.input_impedance(driver_name)
    return float(z.frequency[np.argmax(z.magnitude)])


class TestDriverParameters:
    def test_derived_parameters_are_self_consistent(self):
        d = woofer()
        assert d.fs == pytest.approx(40.0, rel=1e-9)
        assert d.Qms == pytest.approx(3.0, rel=1e-9)
        assert d.Qes == pytest.approx(0.5, rel=1e-9)
        assert d.Qts == pytest.approx(3.0 * 0.5 / 3.5, rel=1e-9)
        assert d.Vas(MEDIUM) == pytest.approx(0.010, rel=1e-9)

    def test_mms_and_vas_routes_agree(self):
        from_vas = woofer()
        from_mms = DriverParameters.from_thiele_small(
            fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=0.0133, Mms=from_vas.Mms, medium=MEDIUM
        )
        assert from_mms.Cms == pytest.approx(from_vas.Cms, rel=1e-9)
        assert from_mms.BL == pytest.approx(from_vas.BL, rel=1e-9)

    def test_requires_exactly_one_of_vas_or_mms(self):
        common = dict(fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=0.0133)
        with pytest.raises(ValueError, match="exactly one"):
            DriverParameters.from_thiele_small(**common)
        with pytest.raises(ValueError, match="exactly one"):
            DriverParameters.from_thiele_small(**common, Vas=0.01, Mms=0.005)

    def test_acoustic_domain_conversions(self):
        d = woofer()
        assert d.Cas == pytest.approx(d.Cms * d.Sd**2)
        assert d.Mas == pytest.approx(d.Mms / d.Sd**2)
        # Vas is the volume of air with the same compliance as the suspension.
        assert d.Cas == pytest.approx(d.Vas(MEDIUM) / (MEDIUM.density * MEDIUM.speed_of_sound**2))

    def test_zero_mechanical_loss_gives_infinite_qms(self):
        d = DriverParameters(Rms=0.0)
        assert math.isinf(d.Qms)
        assert d.Qts == pytest.approx(d.Qes)

    @pytest.mark.parametrize("bad", ["Re", "BL", "Mms", "Cms", "Sd"])
    def test_rejects_nonpositive_parameters(self, bad):
        with pytest.raises(ValueError):
            DriverParameters(**{bad: 0.0})


class TestSealedBox:
    """The canonical validation: a driver in a sealed box has an exact analytic answer."""

    @pytest.mark.parametrize("box_litres", [1.0, 5.0, 20.0, 100.0])
    def test_resonance_matches_closed_form(self, box_litres):
        """fc = fs * sqrt(1 + Vas/Vb), independently derived."""
        d = woofer()
        volume = box_litres / 1000.0
        expected = d.sealed_box_resonance(volume, MEDIUM)

        net = Network(MEDIUM)
        # Front radiates to the reference; back sees only the box, so nothing but the
        # suspension and the box stiffness set the resonance -- exactly the closed form.
        net.add(Driver("drv", d, front_node=GROUND, back_node="BOX", voltage=2.83))
        net.add(Compliance("box", volume, "BOX"))

        f = np.linspace(expected * 0.7, expected * 1.3, 4001)
        assert resonance_from_impedance(net.solve(f), "drv") == pytest.approx(expected, rel=2e-3)

    def test_larger_box_lowers_resonance(self):
        d = woofer()
        assert d.sealed_box_resonance(0.050, MEDIUM) < d.sealed_box_resonance(0.005, MEDIUM)

    def test_infinite_baffle_limit_approaches_free_air(self):
        """A very large box barely stiffens the suspension."""
        d = woofer()
        net = Network(MEDIUM)
        net.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 100.0, "BOX"))  # 100 m^3
        f = np.linspace(d.fs * 0.8, d.fs * 1.2, 2001)
        assert resonance_from_impedance(net.solve(f), "drv") == pytest.approx(d.fs, rel=5e-3)

    def test_response_shape_is_second_order_highpass(self):
        """Radiated pressure goes as volume acceleration; its shape must match theory.

        For a sealed box that shape is a second-order high-pass with the analytic fc and
        Qtc. Checking the whole curve, not just the resonance, exercises damping too.
        """
        d = woofer()
        volume = 0.005
        fc = d.sealed_box_resonance(volume, MEDIUM)
        qtc = d.sealed_box_q(volume, MEDIUM)

        net = Network(MEDIUM)
        net.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", volume, "BOX"))

        f = log_frequencies(10.0, 400.0, 24)
        solution = net.solve(f)
        # Far-field pressure is proportional to volume acceleration, j*omega*U.
        u = solution.volume_velocity("drv").values
        modelled = np.abs(2.0 * math.pi * f * u)

        ratio = f / fc
        expected = ratio**2 / np.sqrt((1.0 - ratio**2) ** 2 + (ratio / qtc) ** 2)

        # Compare normalised shapes in dB.
        modelled_db = 20.0 * np.log10(modelled / modelled[-1])
        expected_db = 20.0 * np.log10(expected / expected[-1])
        assert modelled_db == pytest.approx(expected_db, abs=0.15)

    def test_impedance_peak_exceeds_dc_resistance(self):
        d = woofer()
        net = Network(MEDIUM)
        net.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        z = net.solve(log_frequencies(10.0, 1000.0, 48)).input_impedance("drv")
        assert z.magnitude.max() > d.Re * 2.0
        # Far from resonance the impedance returns to Re.
        assert z.magnitude[-1] == pytest.approx(d.Re, rel=0.1)


class TestVentedBox:
    def test_vent_adds_a_second_impedance_peak(self):
        """A vented box shows two impedance peaks; a sealed one shows a single peak."""
        d = woofer()
        f = log_frequencies(10.0, 300.0, 96)

        sealed = Network(MEDIUM)
        sealed.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        sealed.add(Compliance("box", 0.020, "BOX"))

        vented = Network(MEDIUM)
        vented.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        vented.add(Compliance("box", 0.020, "BOX"))
        vented.add(AcousticMass("port", area=0.0020, length=0.10, node_a="BOX", node_b=GROUND))

        def peak_count(net):
            z = net.solve(f).input_impedance("drv").magnitude
            return int(np.sum((z[1:-1] > z[:-2]) & (z[1:-1] > z[2:])))

        assert peak_count(sealed) == 1
        assert peak_count(vented) == 2

    def test_port_end_correction_lengthens_the_air_slug(self):
        port = AcousticMass("p", area=0.002, length=0.10, node_a="A", node_b=GROUND)
        assert port.effective_length > port.length
        unflanged = AcousticMass("p", area=0.002, length=0.10, node_a="A", flanged_ends=0)
        assert unflanged.effective_length == pytest.approx(0.10)


class TestVentDamping:
    """A vent behind a mesh: the topology that tunes an open-back headphone."""

    def build(self, mesh_rayls: float):
        d = woofer()
        net = Network(MEDIUM)
        net.add(Driver("drv", d, front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        if mesh_rayls is None:  # sealed: no vent at all
            return net
        area = 5.0e-4
        # The mesh is in SERIES with the vent -- air leaving the box passes through both.
        net.add(AcousticMass("vent", area=area, length=0.005, node_a="BOX", node_b="VENT"))
        net.add(Resistance.from_rayls("mesh", mesh_rayls, area, "VENT", GROUND))
        return net

    def test_a_blocked_vent_behaves_like_a_sealed_box(self):
        """The self-consistency check: enough damping and the vent stops existing.

        A very resistive mesh must drive the vented result back onto the sealed one. If
        the mesh were wired in parallel with the vent instead of in series, this would
        fail -- which is how that error was caught in examples/open_back_study.py.
        """
        f = np.linspace(20.0, 400.0, 4001)
        sealed = resonance_from_impedance(self.build(None).solve(f), "drv")
        blocked = resonance_from_impedance(self.build(1.0e5).solve(f), "drv")
        assert blocked == pytest.approx(sealed, rel=1e-3)

    def test_an_open_vent_lowers_the_resonance_below_sealed(self):
        f = np.linspace(20.0, 400.0, 4001)
        sealed = resonance_from_impedance(self.build(None).solve(f), "drv")
        open_vent = resonance_from_impedance(self.build(0.001).solve(f), "drv")
        assert open_vent < sealed

    def test_damping_moves_monotonically_between_the_extremes(self):
        """More damping progressively restores the box's grip on the cone.

        Tracked through low-frequency excursion rather than the impedance peak: a vented
        box has *two* impedance peaks that merge as damping rises, so the taller of them
        swaps over and 'the' resonance is not a monotonic quantity.
        """
        f = np.linspace(20.0, 400.0, 2001)
        excursions = [
            float(self.build(r).solve(f).excursion("drv").magnitude[0])
            for r in (1.0, 10.0, 100.0, 1000.0)
        ]
        assert excursions == sorted(excursions, reverse=True)
        sealed = float(self.build(None).solve(f).excursion("drv").magnitude[0])
        assert excursions[-1] == pytest.approx(sealed, rel=0.05)


class TestMultipleDrivers:
    """Superposition is the tempting shortcut, and it is wrong (§2.4)."""

    def build(self, volume: float, *, shared: bool, voltage_b: float = 2.83, polarity_b: int = 1):
        d = woofer()
        net = Network(MEDIUM)
        net.add(Driver("a", d, front_node=GROUND, back_node="BOX_A", voltage=2.83))
        net.add(Compliance("box_a", volume, "BOX_A"))
        back_b = "BOX_A" if shared else "BOX_B"
        net.add(Driver("b", d, front_node=GROUND, back_node=back_b,
                       voltage=voltage_b, polarity=polarity_b))
        if not shared:
            net.add(Compliance("box_b", volume, "BOX_B"))
        return net

    def test_shared_volume_couples_the_drivers(self):
        """Two drivers in one box do not behave like two drivers in separate boxes.

        Sharing halves the effective compliance each driver sees, so the system resonance
        rises. Superposing two independent single-driver runs cannot show this.
        """
        f = np.linspace(20.0, 200.0, 4001)
        shared = resonance_from_impedance(self.build(0.005, shared=True).solve(f), "a")
        separate = resonance_from_impedance(self.build(0.005, shared=False).solve(f), "a")
        assert shared > separate * 1.05

    def test_shared_volume_matches_the_halved_box_prediction(self):
        """Two identical drivers sharing volume V behave like one driver in V/2."""
        d = woofer()
        f = np.linspace(20.0, 250.0, 6001)
        shared = resonance_from_impedance(self.build(0.010, shared=True).solve(f), "a")
        assert shared == pytest.approx(d.sealed_box_resonance(0.005, MEDIUM), rel=5e-3)

    def test_separate_boxes_do_not_couple(self):
        d = woofer()
        f = np.linspace(20.0, 250.0, 6001)
        separate = resonance_from_impedance(self.build(0.010, shared=False).solve(f), "a")
        assert separate == pytest.approx(d.sealed_box_resonance(0.010, MEDIUM), rel=5e-3)

    def test_opposed_polarity_in_a_shared_volume_stiffens_differently(self):
        """Polarity changes the coupling, not just the sign of the output."""
        f = np.linspace(20.0, 250.0, 4001)
        same = resonance_from_impedance(self.build(0.005, shared=True).solve(f), "a")
        opposed = resonance_from_impedance(
            self.build(0.005, shared=True, polarity_b=-1).solve(f), "a"
        )
        assert opposed != pytest.approx(same, rel=1e-3)

    def test_summed_output_of_two_in_phase_drivers_is_6db_up(self):
        f = log_frequencies(20.0, 500.0, 12)
        net = self.build(0.005, shared=False)
        solution = net.solve(f)
        ua = solution.volume_velocity("a")
        ub = solution.volume_velocity("b")
        from freecad.audio_analysis.results.curve import ResponseCurve

        total = ResponseCurve.sum([ua, ub])
        assert 20.0 * np.log10(np.abs(total.values[0]) / np.abs(ua.values[0])) == pytest.approx(
            6.0206, abs=1e-3
        )

    def test_impedance_is_independent_of_polarity(self):
        f = log_frequencies(20.0, 500.0, 12)
        normal = self.build(0.005, shared=False).solve(f).input_impedance("b")
        flipped = self.build(0.005, shared=False, polarity_b=-1).solve(f).input_impedance("b")
        assert flipped.magnitude == pytest.approx(normal.magnitude, rel=1e-9)


class TestElements:
    def test_compliance_impedance_falls_with_frequency(self):
        c = Compliance("c", 0.001, "A")
        omega = 2 * math.pi * np.array([10.0, 100.0])
        z = np.abs(c.impedance(omega, MEDIUM))
        assert z[1] == pytest.approx(z[0] / 10.0, rel=1e-9)

    def test_mass_impedance_rises_with_frequency(self):
        m = AcousticMass("m", area=0.001, length=0.05, node_a="A")
        omega = 2 * math.pi * np.array([10.0, 100.0])
        z = np.abs(m.impedance(omega, MEDIUM))
        assert z[1] == pytest.approx(z[0] * 10.0, rel=1e-9)

    def test_resistance_is_frequency_independent_and_real(self):
        r = Resistance("r", 1000.0, "A")
        z = r.impedance(2 * math.pi * np.array([10.0, 10000.0]), MEDIUM)
        assert np.all(z.imag == 0.0)
        assert z.real == pytest.approx(1000.0)

    def test_resistance_from_rayls(self):
        r = Resistance.from_rayls("mesh", specific_resistance=100.0, area=0.0001, node_a="A")
        assert r.resistance == pytest.approx(1.0e6)

    def test_leak_resistance_scales_as_gap_cubed(self):
        """Halving the gap raises resistance eightfold -- why seal quality dominates."""
        wide = Leak("l", gap=2e-4, width=0.05, length=0.002, node_a="A")
        narrow = Leak("l", gap=1e-4, width=0.05, length=0.002, node_a="A")
        omega = 2 * math.pi * np.array([100.0])
        assert narrow.impedance(omega, MEDIUM).real[0] == pytest.approx(
            8.0 * wide.impedance(omega, MEDIUM).real[0], rel=1e-9
        )

    def test_piston_radiation_low_frequency_limits(self):
        """Real part goes as (ka)^2/2, imaginary as 8ka/(3 pi)."""
        area = 0.01
        rad = PistonRadiation("rad", area, "A")
        radius = rad.radius
        f = np.array([20.0])
        omega = 2 * math.pi * f
        k = omega / MEDIUM.speed_of_sound
        z = rad.impedance(omega, MEDIUM)
        scale = MEDIUM.density * MEDIUM.speed_of_sound / area
        assert z.real[0] == pytest.approx(scale * (k[0] * radius) ** 2 / 2.0, rel=2e-3)
        assert z.imag[0] == pytest.approx(scale * 8.0 * k[0] * radius / (3.0 * math.pi), rel=2e-3)

    def test_piston_radiation_is_finite_at_low_frequency(self):
        rad = PistonRadiation("rad", 0.01, "A")
        z = rad.impedance(2 * math.pi * np.array([1e-3]), MEDIUM)
        assert np.all(np.isfinite(z))

    def test_passive_radiator_resonance(self):
        pr = PassiveRadiator("pr", mass=0.02, compliance=5e-4, area=0.01, node_a="A")
        assert pr.resonance == pytest.approx(1.0 / (2 * math.pi * math.sqrt(0.02 * 5e-4)))

    def test_element_rejects_self_connection(self):
        with pytest.raises(ValueError, match="both terminals"):
            Compliance("c", 0.001, "A", "A")


class TestNetworkValidation:
    def test_empty_network_raises(self):
        with pytest.raises(ValueError, match="empty network"):
            Network(MEDIUM).solve([100.0])

    def test_duplicate_element_names_rejected(self):
        net = Network(MEDIUM)
        net.add(Compliance("x", 0.001, "A", "B"))
        with pytest.raises(ValueError, match="duplicate"):
            net.add(Resistance("x", 100.0, "A", "B"))

    def test_floating_node_gives_a_comprehensible_error(self):
        """A port that goes nowhere is a wiring mistake, not a singular matrix."""
        net = Network(MEDIUM)
        net.add(Driver("drv", woofer(), front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        net.add(AcousticMass("dangling", area=0.001, length=0.05, node_a="BOX", node_b="NOWHERE"))
        with pytest.raises(ValueError, match="NOWHERE"):
            net.solve([100.0])

    def test_unknown_element_lookup_lists_options(self):
        net = Network(MEDIUM)
        net.add(Compliance("box", 0.001, "A", "B"))
        with pytest.raises(KeyError, match="box"):
            net.element("nope")

    def test_rejects_nonpositive_frequency(self):
        net = Network(MEDIUM)
        net.add(Driver("drv", woofer(), front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        with pytest.raises(ValueError, match="positive"):
            net.solve([0.0, 100.0])

    def test_node_names_are_stable(self):
        net = Network(MEDIUM)
        net.add(Driver("drv", woofer(), front_node="EAR", back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        net.add(Compliance("ear", 1e-5, "EAR"))
        assert net.node_names() == ["EAR", "BOX"]


class TestSolutionOutputs:
    def sealed(self):
        net = Network(MEDIUM)
        net.add(Driver("drv", woofer(), front_node=GROUND, back_node="BOX"))
        net.add(Compliance("box", 0.005, "BOX"))
        return net

    def test_excursion_is_largest_at_low_frequency(self):
        f = log_frequencies(10.0, 1000.0, 24)
        x = self.sealed().solve(f).excursion("drv")
        assert x.magnitude[0] > x.magnitude[-1]

    def test_excursion_is_below_xmax_at_moderate_drive(self):
        f = log_frequencies(20.0, 500.0, 12)
        x = self.sealed().solve(f).excursion("drv")
        assert x.magnitude.max() < woofer().Xmax

    def test_curves_carry_the_validity_limit(self):
        f = log_frequencies(20.0, 500.0, 12)
        solution = self.sealed().solve(f, valid_below=407.0)
        assert solution.pressure("BOX").valid_below == 407.0
        assert solution.excursion("drv").valid_below == 407.0

    def test_curves_carry_medium_provenance(self):
        f = log_frequencies(20.0, 500.0, 12)
        metadata = self.sealed().solve(f).pressure("BOX").metadata
        assert metadata["solver"] == "lumped network"
        assert "343" in metadata["speed of sound"]

    def test_unknown_node_lookup_raises(self):
        f = log_frequencies(20.0, 500.0, 12)
        with pytest.raises(KeyError, match="EAR"):
            self.sealed().solve(f).pressure("EAR")

    def test_excursion_of_a_non_driver_raises(self):
        f = log_frequencies(20.0, 500.0, 12)
        with pytest.raises(TypeError):
            self.sealed().solve(f).excursion("box")
