"""Unit conversion checks.

These look trivial, and that is the point. FreeCAD is mm, every solver is SI, and a
misplaced factor of 1000 produces a plausible wrong answer rather than a crash. Pinning
the conversions with tests is cheap insurance (CLAUDE.md, "Units").
"""

from __future__ import annotations

import pytest

from freecad.audio_analysis.physics import units


def test_length():
    assert units.mm_to_m(1000.0) == pytest.approx(1.0)
    assert units.m_to_mm(1.0) == pytest.approx(1000.0)


def test_area_uses_squared_factor():
    # The classic mistake: dividing area by 1000 instead of 1e6.
    assert units.mm2_to_m2(1.0e6) == pytest.approx(1.0)
    assert units.m2_to_mm2(1.0) == pytest.approx(1.0e6)


def test_volume_uses_cubed_factor():
    assert units.mm3_to_m3(1.0e9) == pytest.approx(1.0)
    assert units.m3_to_mm3(1.0) == pytest.approx(1.0e9)


def test_litres():
    # A 1 litre box is 1e6 mm^3 -- e.g. a 100x100x100 mm cube.
    assert units.mm3_to_litre(100.0 * 100.0 * 100.0) == pytest.approx(1.0)
    assert units.litre_to_m3(1000.0) == pytest.approx(1.0)


def test_a_typical_sealed_box():
    # 5 litre enclosure, a common bookshelf woofer volume.
    volume_mm3 = 5.0e6
    assert units.mm3_to_litre(volume_mm3) == pytest.approx(5.0)
    assert units.mm3_to_m3(volume_mm3) == pytest.approx(0.005)


def test_a_typical_ear_canal_volume():
    # The occluded ear volume is around 1 cm^3 = 1000 mm^3 -- six orders of magnitude
    # smaller than a loudspeaker box. Both must come out right.
    assert units.mm3_to_m3(1000.0) == pytest.approx(1.0e-6)
    assert units.mm3_to_litre(1000.0) == pytest.approx(0.001)


def test_internal_pressure_is_kilopascals():
    # The trap: FreeCAD's base units (mm, kg, s) make its internal pressure unit kPa.
    # An App::PropertyPressure set to "101325 Pa" reads back as 101.325.
    assert units.internal_pressure_to_pa(101.325) == pytest.approx(101325.0)
    assert units.pa_to_internal_pressure(101325.0) == pytest.approx(101.325)


@pytest.mark.parametrize("value", [0.0, 1.0, 1234.5, 1e9])
def test_round_trips(value):
    assert units.m_to_mm(units.mm_to_m(value)) == pytest.approx(value)
    assert units.m2_to_mm2(units.mm2_to_m2(value)) == pytest.approx(value)
    assert units.m3_to_mm3(units.mm3_to_m3(value)) == pytest.approx(value)
