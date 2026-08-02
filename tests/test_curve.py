"""Response curve tests.

Exercised against analytically known cases, so a regression shows up as a wrong number
rather than a plausible-looking plot.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.results.curve import ResponseCurve, log_frequencies


def flat_curve(level_pa: float = 1.0, n: int = 50, label: str = "flat") -> ResponseCurve:
    f = log_frequencies(20.0, 20000.0, points_per_octave=n // 10 or 1)
    return ResponseCurve(f, np.full(f.shape, level_pa, dtype=complex), label=label)


class TestConstruction:
    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            ResponseCurve(np.array([1.0, 2.0]), np.array([1.0 + 0j]))

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="at least one"):
            ResponseCurve(np.array([]), np.array([]))

    def test_rejects_nonpositive_frequency(self):
        with pytest.raises(ValueError, match="positive"):
            ResponseCurve(np.array([0.0, 1.0]), np.array([1 + 0j, 1 + 0j]))

    def test_rejects_unsorted_frequency(self):
        with pytest.raises(ValueError, match="increasing"):
            ResponseCurve(np.array([100.0, 50.0]), np.array([1 + 0j, 1 + 0j]))

    def test_accepts_plain_lists(self):
        curve = ResponseCurve([100.0, 200.0], [1 + 0j, 2 + 0j])
        assert curve.magnitude[1] == pytest.approx(2.0)


class TestDerivedQuantities:
    def test_one_pascal_is_94db(self):
        assert flat_curve(1.0).spl[0] == pytest.approx(93.979, abs=1e-3)

    def test_spl_rejects_non_pressure_quantities(self):
        curve = ResponseCurve([100.0, 200.0], [8 + 0j, 8 + 0j], quantity="impedance", unit="ohm")
        with pytest.raises(ValueError, match="only defined for pressure"):
            _ = curve.spl

    def test_spl_survives_perfect_cancellation(self):
        # Two drivers exactly out of phase produce a true zero; this must not raise or
        # return NaN, since a cancellation notch is a real and interesting result.
        curve = ResponseCurve([100.0, 200.0], [0 + 0j, 1 + 0j])
        assert np.isfinite(curve.spl).all()
        assert curve.spl[0] < -100.0

    def test_phase_is_unwrapped(self):
        # A pure delay winds phase past -180 deg; unwrapping keeps it monotonic.
        f = np.linspace(100.0, 2000.0, 200)
        delay = 2e-3
        curve = ResponseCurve(f, np.exp(-2j * math.pi * f * delay))
        assert np.all(np.diff(curve.phase_rad) < 0)

    def test_group_delay_of_a_pure_delay(self):
        # The definitive check: a 2 ms delay must read as 2 ms at every frequency.
        f = np.linspace(100.0, 2000.0, 400)
        delay = 2e-3
        curve = ResponseCurve(f, np.exp(-2j * math.pi * f * delay))
        assert curve.group_delay == pytest.approx(np.full(f.shape, delay), rel=1e-6)

    def test_group_delay_of_a_flat_curve_is_zero(self):
        assert flat_curve().group_delay == pytest.approx(0.0, abs=1e-12)


class TestSummation:
    def test_two_in_phase_drivers_add_6db(self):
        a, b = flat_curve(1.0, label="A"), flat_curve(1.0, label="B")
        summed = ResponseCurve.sum([a, b])
        assert summed.spl[0] - a.spl[0] == pytest.approx(6.0206, abs=1e-3)

    def test_reversed_polarity_cancels(self):
        """The result that makes complex summation non-negotiable."""
        a = flat_curve(1.0, label="A")
        summed = ResponseCurve.sum([a, a.inverted()])
        assert summed.magnitude == pytest.approx(0.0, abs=1e-15)

    def test_quadrature_sum_is_3db(self):
        # 90 degrees apart: powers add, not amplitudes.
        f = np.array([100.0, 200.0])
        a = ResponseCurve(f, np.array([1 + 0j, 1 + 0j]))
        b = ResponseCurve(f, np.array([0 + 1j, 0 + 1j]))
        assert ResponseCurve.sum([a, b]).magnitude[0] == pytest.approx(math.sqrt(2.0))

    def test_sum_inherits_the_strictest_validity_limit(self):
        f = np.array([100.0, 200.0])
        a = ResponseCurve(f, np.array([1 + 0j, 1 + 0j]), valid_below=400.0)
        b = ResponseCurve(f, np.array([1 + 0j, 1 + 0j]), valid_below=1000.0)
        assert ResponseCurve.sum([a, b]).valid_below == 400.0

    def test_sum_rejects_mismatched_axes(self):
        a = ResponseCurve([100.0, 200.0], [1 + 0j, 1 + 0j])
        b = ResponseCurve([100.0, 300.0], [1 + 0j, 1 + 0j])
        with pytest.raises(ValueError, match="same frequency axis"):
            ResponseCurve.sum([a, b])

    def test_sum_rejects_mixed_quantities(self):
        f = np.array([100.0, 200.0])
        a = ResponseCurve(f, np.array([1 + 0j, 1 + 0j]), quantity="pressure")
        b = ResponseCurve(f, np.array([1 + 0j, 1 + 0j]), quantity="impedance")
        with pytest.raises(ValueError, match="cannot sum"):
            ResponseCurve.sum([a, b])

    def test_sum_of_nothing_raises(self):
        with pytest.raises(ValueError, match="nothing to sum"):
            ResponseCurve.sum([])


class TestSmoothing:
    def test_smoothing_leaves_a_flat_curve_flat(self):
        curve = flat_curve(1.0)
        assert curve.smooth(6).magnitude == pytest.approx(curve.magnitude)

    def test_smoothing_reduces_a_narrow_spike(self):
        f = log_frequencies(20.0, 20000.0, 48)
        values = np.ones(f.shape, dtype=complex)
        values[len(f) // 2] = 10.0
        curve = ResponseCurve(f, values)
        assert curve.smooth(3).magnitude.max() < 10.0

    def test_smoothing_preserves_phase(self):
        f = np.linspace(100.0, 2000.0, 200)
        curve = ResponseCurve(f, np.exp(-2j * math.pi * f * 1e-3))
        assert curve.smooth(6).phase_rad == pytest.approx(curve.phase_rad)

    def test_rejects_nonpositive_fraction(self):
        with pytest.raises(ValueError):
            flat_curve().smooth(0)


class TestValidity:
    def test_trusted_truncates_at_the_limit(self):
        f = log_frequencies(20.0, 20000.0, 12)
        curve = ResponseCurve(f, np.ones(f.shape, dtype=complex), valid_below=407.0)
        assert curve.trusted().frequency.max() <= 407.0
        assert curve.frequency.max() > 407.0  # original untouched

    def test_no_limit_means_the_whole_curve(self):
        curve = flat_curve()
        assert curve.trusted().frequency.size == curve.frequency.size

    def test_curve_entirely_above_its_limit_raises(self):
        curve = ResponseCurve([1000.0, 2000.0], [1 + 0j, 1 + 0j], valid_below=100.0)
        with pytest.raises(ValueError, match="validity limit"):
            curve.trusted()


class TestInterpolation:
    def test_at_returns_exact_sample_values(self):
        curve = ResponseCurve([100.0, 1000.0], [1 + 0j, 3 + 0j])
        assert curve.at(100.0) == pytest.approx(1 + 0j)
        assert curve.at(1000.0) == pytest.approx(3 + 0j)

    def test_at_interpolates_logarithmically(self):
        # 316 Hz is the geometric midpoint of 100 and 1000.
        curve = ResponseCurve([100.0, 1000.0], [1 + 0j, 3 + 0j])
        assert curve.at(math.sqrt(100.0 * 1000.0)).real == pytest.approx(2.0, rel=1e-6)

    def test_spl_at(self):
        assert flat_curve(1.0).spl_at(1000.0) == pytest.approx(93.979, abs=1e-3)


class TestExport:
    def test_csv_has_header_metadata_and_data(self, tmp_path):
        curve = ResponseCurve(
            [100.0, 200.0], [1 + 0j, 2 + 0j],
            label="woofer", valid_below=407.0, metadata={"solver": "lumped", "drive": "1 V"},
        )
        path = tmp_path / "out.csv"
        curve.to_csv(str(path))
        text = path.read_text()
        assert "# woofer" in text
        assert "# solver: lumped" in text
        assert "# valid below: 407.0 Hz" in text
        assert "SPL_dB" in text
        # label + 2 metadata + validity + column names = 5 header lines, then 2 data rows
        assert len(text.strip().splitlines()) == 7

    def test_frd_round_trips_through_numpy(self, tmp_path):
        curve = flat_curve(1.0, label="response")
        path = tmp_path / "out.frd"
        curve.to_frd(str(path))
        data = np.loadtxt(str(path), comments="*")
        assert data.shape[1] == 3
        assert data[:, 1] == pytest.approx(93.979, abs=1e-3)

    def test_frd_rejects_non_pressure(self, tmp_path):
        curve = ResponseCurve([100.0, 200.0], [8 + 0j, 8 + 0j], quantity="impedance")
        with pytest.raises(ValueError, match="FRD holds pressure"):
            curve.to_frd(str(tmp_path / "bad.frd"))


class TestLogFrequencies:
    def test_spans_the_requested_range(self):
        f = log_frequencies(20.0, 20000.0)
        assert f[0] == pytest.approx(20.0)
        assert f[-1] == pytest.approx(20000.0)

    def test_constant_resolution_per_octave(self):
        f = log_frequencies(20.0, 20480.0, points_per_octave=12)
        ratios = f[1:] / f[:-1]
        assert ratios == pytest.approx(np.full(ratios.shape, ratios[0]), rel=1e-9)

    def test_audio_band_point_count(self):
        # 20 Hz to 20 kHz is ~9.97 octaves; at 24 points/octave that is ~240 points.
        assert 235 < log_frequencies(20.0, 20000.0, 24).size < 245

    @pytest.mark.parametrize("start,stop", [(0.0, 100.0), (100.0, 100.0), (200.0, 100.0)])
    def test_rejects_invalid_ranges(self, start, stop):
        with pytest.raises(ValueError):
            log_frequencies(start, stop)
