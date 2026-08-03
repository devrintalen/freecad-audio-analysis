"""Target curves and sensitivity.

For a headphone the target is the whole argument: there is no room and no reference except
an agreed curve, so "is this right" means "how far is it from the target". Two properties
of the comparison are load-bearing and tested hard: it must compare *shape* rather than
level, and it must refuse to quote a figure over a band it has no information about.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from freecad.audio_analysis.results.curve import ResponseCurve, log_frequencies
from freecad.audio_analysis.results.summary import sensitivity
from freecad.audio_analysis.results.target import (
    TargetCurve,
    TargetError,
    compare,
    load_target,
)


def curve_from_db(frequency: np.ndarray, spl: np.ndarray, **kwargs) -> ResponseCurve:
    """A pressure curve at a given SPL, for comparing against a target."""
    from freecad.audio_analysis.physics import air

    magnitude = air.P_REF * 10.0 ** (np.asarray(spl, dtype=float) / 20.0)
    return ResponseCurve(frequency, magnitude.astype(complex), **kwargs)


FREQUENCY = log_frequencies(20.0, 20000.0, 12)


# ---------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------


class TestLoading:
    def test_reads_an_frd(self, tmp_path):
        path = tmp_path / "target.frd"
        path.write_text("* a comment\n20 80.0 0\n1000 74.0 -30\n20000 60.0 -90\n")
        target = load_target(str(path))
        assert target.frequency.tolist() == [20.0, 1000.0, 20000.0]
        assert target.level_db.tolist() == [80.0, 74.0, 60.0]
        assert target.label == "target"

    def test_reads_a_csv_with_a_header(self, tmp_path):
        path = tmp_path / "harmanish.csv"
        path.write_text("frequency_Hz,phase_deg,SPL_dB\n20,0,80\n1000,-30,74\n")
        target = load_target(str(path))
        # The SPL column is found by name, not by position.
        assert target.level_db.tolist() == [80.0, 74.0]

    def test_reads_a_csv_without_a_header(self, tmp_path):
        path = tmp_path / "plain.csv"
        path.write_text("20,80\n1000,74\n")
        assert load_target(str(path)).level_db.tolist() == [80.0, 74.0]

    def test_skips_comments_and_blank_lines(self, tmp_path):
        path = tmp_path / "messy.frd"
        path.write_text("# note\n\n* another\n;semicolon\n20 80\n1000 74\n")
        assert load_target(str(path)).frequency.size == 2

    def test_points_are_sorted_by_frequency(self, tmp_path):
        path = tmp_path / "unsorted.csv"
        path.write_text("1000,74\n20,80\n200,78\n")
        target = load_target(str(path))
        assert target.frequency.tolist() == [20.0, 200.0, 1000.0]
        assert target.level_db.tolist() == [80.0, 78.0, 74.0]

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(TargetError, match="no such file"):
            load_target(str(tmp_path / "absent.csv"))

    def test_an_unusable_file_explains_the_format(self, tmp_path):
        path = tmp_path / "prose.txt"
        path.write_text("This is not a curve.\nIt is a sentence.\n")
        with pytest.raises(TargetError, match="frequency in Hz"):
            load_target(str(path))

    def test_one_point_is_not_a_curve(self, tmp_path):
        path = tmp_path / "single.csv"
        path.write_text("1000,74\n")
        with pytest.raises(TargetError):
            load_target(str(path))


class TestInterpolation:
    def test_interpolates_on_a_log_axis(self):
        """A target is written per octave, so the midpoint of 100 and 10000 is 1000."""
        target = TargetCurve(np.array([100.0, 10000.0]), np.array([80.0, 60.0]))
        assert target.at(np.array([1000.0]))[0] == pytest.approx(70.0)

    def test_returns_exact_values_at_its_own_points(self):
        target = TargetCurve(np.array([100.0, 1000.0]), np.array([80.0, 60.0]))
        assert target.at(target.frequency).tolist() == [80.0, 60.0]


# ---------------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------------


class TestComparison:
    def test_a_matching_shape_deviates_by_nothing(self):
        """Even at a completely different level: a target fixes shape, not loudness."""
        shape = 80.0 - 10.0 * np.log10(FREQUENCY / 20.0)
        target = TargetCurve(FREQUENCY, shape)
        measured = curve_from_db(FREQUENCY, shape + 23.0)

        deviation = compare(measured, target)
        assert deviation.rms == pytest.approx(0.0, abs=1e-9)
        assert deviation.offset_db == pytest.approx(-23.0)

    def test_the_offset_is_reported_so_the_level_match_is_visible(self):
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        deviation = compare(curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, 100.0)), target)
        assert deviation.offset_db == pytest.approx(-30.0)
        assert "level-matched by -30.0 dB" in deviation.format()

    def test_a_tilt_shows_up_as_deviation(self):
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        tilted = 70.0 + 6.0 * np.log2(FREQUENCY / FREQUENCY[0]) / np.log2(1000.0 / 20.0)
        deviation = compare(curve_from_db(FREQUENCY, tilted), target)
        assert deviation.rms > 1.0
        where, amount = deviation.worst
        assert where > 1000.0 and amount > 0  # too much treble

    def test_the_band_is_clipped_to_the_validity_limit(self):
        """A deviation figure quoted past the limit is not evidence."""
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        measured = curve_from_db(
            FREQUENCY, np.full(FREQUENCY.shape, 70.0), valid_below=400.0
        )
        assert compare(measured, target).band[1] <= 400.0

    def test_the_band_is_clipped_to_the_target(self):
        target = TargetCurve(np.array([100.0, 2000.0]), np.array([70.0, 70.0]))
        deviation = compare(curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, 70.0)), target)
        assert deviation.band[0] >= 100.0
        assert deviation.band[1] <= 2000.0

    def test_an_explicit_band_narrows_further(self):
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        measured = curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        deviation = compare(measured, target, band=(200.0, 2000.0))
        assert deviation.band[0] >= 200.0
        assert deviation.band[1] <= 2000.0

    def test_no_overlap_is_an_error_that_says_the_two_ranges(self):
        target = TargetCurve(np.array([30000.0, 40000.0]), np.array([70.0, 70.0]))
        measured = curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        with pytest.raises(TargetError, match="no overlap"):
            compare(measured, target)

    def test_a_validity_limit_below_the_target_is_an_error_not_a_guess(self):
        target = TargetCurve(np.array([1000.0, 20000.0]), np.array([70.0, 70.0]))
        measured = curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, 70.0), valid_below=400.0)
        with pytest.raises(TargetError, match="valid to 400"):
            compare(measured, target)

    def test_only_pressure_can_be_compared(self):
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        impedance = ResponseCurve(
            FREQUENCY, np.ones(FREQUENCY.shape, dtype=complex), quantity="impedance"
        )
        with pytest.raises(TargetError, match="not 'impedance'"):
            compare(impedance, target)

    def test_the_report_names_the_band_and_the_worst_point(self):
        target = TargetCurve(FREQUENCY, np.full(FREQUENCY.shape, 70.0))
        bumped = np.full(FREQUENCY.shape, 70.0)
        bumped[np.argmin(np.abs(FREQUENCY - 100.0))] = 80.0
        deviation = compare(curve_from_db(FREQUENCY, bumped), target)
        text = deviation.format()
        assert "dB RMS over" in text
        assert "too much there" in text


# ---------------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------------


class TestSensitivity:
    def flat(self, spl: float = 100.0, **kwargs) -> ResponseCurve:
        return curve_from_db(FREQUENCY, np.full(FREQUENCY.shape, spl), **kwargs)

    def test_per_volt_scales_out_the_drive_voltage(self):
        """Halving the drive halves the output, so the sensitivity is unchanged."""
        loud = sensitivity(self.flat(100.0), voltage=1.0, resistance=32.0)
        quiet = sensitivity(self.flat(94.0), voltage=0.5, resistance=32.0)
        assert loud.split(",")[0] == quiet.split(",")[0]

    def test_per_milliwatt_follows_the_impedance(self):
        """A 32 ohm driver reads 15 dB lower per milliwatt; a 300 ohm one, 5 dB lower still."""
        for resistance, expected in ((32.0, -14.95), (300.0, -5.23)):
            text = sensitivity(self.flat(100.0), voltage=1.0, resistance=resistance)
            per_volt = float(text.split("sensitivity ")[1].split(" dB/V")[0])
            per_mw = float(text.split(", ")[1].split(" dB/mW")[0])
            # Both figures are read back from a one-decimal display, so the difference
            # carries up to half a display step of rounding either way.
            assert per_mw - per_volt == pytest.approx(expected, abs=0.06)

    def test_it_is_quoted_inside_the_validity_limit(self):
        """The conventional 1 kHz sits well above where a lumped over-ear model holds."""
        text = sensitivity(self.flat(100.0, valid_below=400.0), voltage=1.0, resistance=32.0)
        quoted = float(text.split(" at ")[1].split(" Hz")[0])
        assert quoted <= 400.0
        assert "below the usual 1 kHz" in text

    def test_the_conventional_frequency_is_used_when_it_is_valid(self):
        text = sensitivity(self.flat(100.0), voltage=1.0, resistance=32.0)
        assert " at 1000 Hz" in text
        assert "below the usual" not in text

    def test_nonsense_inputs_are_refused(self):
        with pytest.raises(ValueError, match="must both be positive"):
            sensitivity(self.flat(), voltage=0.0, resistance=32.0)
        with pytest.raises(ValueError, match="must both be positive"):
            sensitivity(self.flat(), voltage=1.0, resistance=-1.0)

    def test_only_pressure_has_a_sensitivity(self):
        impedance = ResponseCurve(
            FREQUENCY, np.ones(FREQUENCY.shape, dtype=complex), quantity="impedance"
        )
        with pytest.raises(ValueError, match="needs a pressure curve"):
            sensitivity(impedance, voltage=1.0, resistance=32.0)


class TestSolutionSummary:
    """Sensitivity belongs to the product, not to each driver."""

    def two_way(self, voltage_b: float = 0.1):
        from freecad.audio_analysis.physics import air as air_module
        from freecad.audio_analysis.physics.driver import DriverParameters
        from freecad.audio_analysis.physics.network import Compliance, Driver, Network

        parameters = DriverParameters.from_thiele_small(
            name="d", fs=60.0, Re=32.0, Qms=3.0, Qes=0.6, Sd=20e-4, Vas=1.5e-3
        )
        network = Network(air_module.AirProperties.at())
        network.add(Driver("A", parameters, front_node="Ear", voltage=0.1))
        network.add(Driver("B", parameters, front_node="Ear", voltage=voltage_b))
        network.add(Compliance("ear", 100e-6, "Ear"))
        return network.solve(log_frequencies(20.0, 2000.0, 12), valid_below=400.0)

    def test_sensitivity_is_reported_once_for_the_system(self):
        from freecad.audio_analysis.results.summary import summarise_solution

        text = summarise_solution(self.two_way())
        assert text.count("sensitivity") == 1
        assert "System:" in text

    def test_separate_amplifiers_get_no_combined_figure(self):
        """Two drivers on different voltages are not one product with one sensitivity."""
        from freecad.audio_analysis.results.summary import summarise_solution

        text = summarise_solution(self.two_way(voltage_b=0.5))
        assert "sensitivity" not in text

    def test_a_single_driver_still_gets_one(self):
        from freecad.audio_analysis.physics import air as air_module
        from freecad.audio_analysis.physics.driver import DriverParameters
        from freecad.audio_analysis.physics.network import Compliance, Driver, Network
        from freecad.audio_analysis.results.summary import summarise_solution

        parameters = DriverParameters.from_thiele_small(
            name="d", fs=60.0, Re=32.0, Qms=3.0, Qes=0.6, Sd=20e-4, Vas=1.5e-3
        )
        network = Network(air_module.AirProperties.at())
        network.add(Driver("A", parameters, front_node="Ear", voltage=0.1))
        network.add(Compliance("ear", 100e-6, "Ear"))
        solution = network.solve(log_frequencies(20.0, 2000.0, 12), valid_below=400.0)
        assert "sensitivity" in summarise_solution(solution)
