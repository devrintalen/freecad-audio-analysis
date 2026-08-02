"""Air property checks against published reference values.

Reference numbers are the textbook values for air at 20 C and one standard atmosphere.
Tolerances are tight deliberately: these feed every downstream acoustic result, so drift
here would show up as a small, plausible, hard-to-trace error everywhere else.
"""

from __future__ import annotations

import math

import pytest

from freecad.audio_analysis.physics import air

T20 = air.from_celsius(20.0)
P_ATM = air.DEFAULT_PRESSURE


def test_celsius_round_trip():
    assert air.to_celsius(air.from_celsius(21.5)) == pytest.approx(21.5)
    assert air.from_celsius(0.0) == pytest.approx(273.15)


def test_saturation_vapour_pressure_at_20c():
    # Buck equation; standard tables give 2339 Pa at 20 C.
    assert air.saturation_vapour_pressure(T20) == pytest.approx(2339.0, rel=1e-3)


def test_dry_air_density_at_room_conditions():
    # Textbook value: 1.204 kg/m^3 at 20 C, 101325 Pa, dry.
    assert air.density(T20, P_ATM, 0.0) == pytest.approx(1.2041, rel=1e-3)


def test_dry_air_speed_of_sound_at_room_conditions():
    # Textbook value: 343.2 m/s at 20 C.
    assert air.speed_of_sound(T20, P_ATM, 0.0) == pytest.approx(343.2, rel=1e-3)


def test_speed_of_sound_scales_with_root_temperature():
    # c is proportional to sqrt(T) for an ideal gas; check against 0 C.
    c0 = air.speed_of_sound(air.from_celsius(0.0), P_ATM, 0.0)
    assert c0 == pytest.approx(331.3, rel=2e-3)
    assert air.speed_of_sound(T20, P_ATM, 0.0) / c0 == pytest.approx(math.sqrt(T20 / 273.15), rel=1e-3)


def test_humid_air_is_less_dense_than_dry_air():
    # Water vapour is lighter than dry air. Counter-intuitive but correct.
    assert air.density(T20, P_ATM, 1.0) < air.density(T20, P_ATM, 0.0)


def test_humidity_raises_speed_of_sound_slightly():
    dry = air.speed_of_sound(T20, P_ATM, 0.0)
    wet = air.speed_of_sound(T20, P_ATM, 1.0)
    assert wet > dry
    # The effect is real but small: a few tenths of a m/s at room temperature.
    assert 0.1 < (wet - dry) < 1.5


def test_humidity_is_clamped_not_rejected():
    # A user typing "50" for 50% should get saturated air, not a mid-sweep exception.
    assert air.water_mole_fraction(T20, P_ATM, 50.0) == air.water_mole_fraction(T20, P_ATM, 1.0)
    assert air.water_mole_fraction(T20, P_ATM, -3.0) == 0.0


def test_viscosity_and_conductivity_at_20c():
    assert air.dynamic_viscosity(T20) == pytest.approx(1.81e-5, rel=1e-2)
    assert air.thermal_conductivity(T20) == pytest.approx(0.0257, rel=1e-2)


def test_prandtl_number_of_air():
    # Air sits near 0.71 across the whole range of interest.
    assert air.prandtl_number(T20, P_ATM, 0.0) == pytest.approx(0.71, rel=2e-2)


class TestAirProperties:
    def test_characteristic_impedance(self):
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        # rho*c is about 413 rayl at room conditions.
        assert props.characteristic_impedance == pytest.approx(413.3, rel=1e-3)

    def test_wavelength_at_20k_is_about_17mm(self):
        # This is why full-band 3D acoustic models are expensive (STRUCTURE.md 2.4).
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.wavelength(20000.0) == pytest.approx(0.01716, rel=1e-2)

    def test_boundary_layer_thicknesses_match_design_doc(self):
        # STRUCTURE.md 2.2 quotes ~70 um viscous and ~82 um thermal at 1 kHz.
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.viscous_boundary_layer(1000.0) == pytest.approx(69e-6, rel=5e-2)
        assert props.thermal_boundary_layer(1000.0) == pytest.approx(82e-6, rel=5e-2)

    def test_thermal_layer_is_thicker_than_viscous(self):
        # Because Pr < 1 for air.
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.thermal_boundary_layer(1000.0) > props.viscous_boundary_layer(1000.0)

    def test_boundary_layer_scales_as_inverse_root_frequency(self):
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        # 100 Hz layer should be sqrt(10) thicker than the 1 kHz one -- about 220 um,
        # which is why sub-millimetre slots are lossy at the bottom of the band.
        ratio = props.viscous_boundary_layer(100.0) / props.viscous_boundary_layer(1000.0)
        assert ratio == pytest.approx(math.sqrt(10.0), rel=1e-6)
        assert props.viscous_boundary_layer(100.0) == pytest.approx(219e-6, rel=5e-2)

    def test_helmholtz_number_is_one_at_the_expected_size(self):
        # ka == 1 when the radius equals lambda/(2*pi).
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        radius = props.wavelength(1000.0) / (2.0 * math.pi)
        assert props.helmholtz_number(2.0 * radius, 1000.0) == pytest.approx(1.0)

    def test_lumped_validity_limit_of_an_over_ear_cup(self):
        # A 105 mm cup stops being a lumped volume around 400 Hz. This is the number
        # that says how much of a two-way headphone Tier 1 can honestly cover.
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.lumped_validity_limit(0.1056) == pytest.approx(406.0, rel=2e-2)

    def test_lumped_validity_limit_of_an_ear_canal_sized_cavity(self):
        # A 10 mm cavity stays lumped to roughly 4 kHz -- an order of magnitude more
        # headroom, which is why in-ear lumped models are useful further up the band.
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.lumped_validity_limit(0.010) == pytest.approx(4290.0, rel=2e-2)

    def test_lumped_validity_scales_inversely_with_size(self):
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.lumped_validity_limit(0.05) == pytest.approx(
            2.0 * props.lumped_validity_limit(0.1)
        )

    def test_stricter_fraction_lowers_the_limit(self):
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        assert props.lumped_validity_limit(0.1, fraction=1 / 16) < props.lumped_validity_limit(0.1)

    @pytest.mark.parametrize("bad", [0.0, -0.1])
    def test_rejects_nonphysical_dimension(self, bad):
        props = air.AirProperties.at()
        with pytest.raises(ValueError):
            props.lumped_validity_limit(bad)
        with pytest.raises(ValueError):
            props.helmholtz_number(bad, 1000.0)

    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5])
    def test_rejects_invalid_fraction(self, bad):
        props = air.AirProperties.at()
        with pytest.raises(ValueError):
            props.lumped_validity_limit(0.1, fraction=bad)

    def test_mesh_size_at_20k(self):
        props = air.AirProperties.at(T20, P_ATM, 0.0)
        # ~2 mm elements to resolve 20 kHz at 8 elements per wavelength.
        assert props.mesh_size_for(20000.0) == pytest.approx(0.00215, rel=5e-2)

    def test_defaults_are_room_conditions(self):
        props = air.AirProperties.at()
        assert props.speed_of_sound == pytest.approx(343.4, rel=2e-3)
        assert props.density == pytest.approx(1.199, rel=2e-3)

    def test_is_frozen(self):
        props = air.AirProperties.at()
        with pytest.raises(Exception):
            props.density = 1.0  # type: ignore[misc]

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_rejects_nonphysical_temperature(self, bad):
        with pytest.raises(ValueError):
            air.AirProperties.at(temperature=bad)

    @pytest.mark.parametrize("bad", [0.0, -101325.0])
    def test_rejects_nonphysical_pressure(self, bad):
        with pytest.raises(ValueError):
            air.AirProperties.at(pressure=bad)

    @pytest.mark.parametrize("bad", [0.0, -100.0])
    def test_rejects_nonphysical_frequency(self, bad):
        props = air.AirProperties.at()
        with pytest.raises(ValueError):
            props.wavelength(bad)
        with pytest.raises(ValueError):
            props.viscous_boundary_layer(bad)


class TestSpl:
    def test_reference_pressure_is_zero_db(self):
        assert air.pressure_to_spl(air.P_REF) == pytest.approx(0.0, abs=1e-9)

    def test_94db_is_one_pascal(self):
        # The calibration point every acoustics engineer knows. Exactly it is
        # 20*log10(1/20e-6) = 93.979 dB; "94 dB" is the conventional rounding.
        assert air.pressure_to_spl(1.0) == pytest.approx(93.979, abs=1e-3)
        assert air.spl_to_pressure(94.0) == pytest.approx(1.0, rel=3e-3)

    def test_round_trip(self):
        for spl in (0.0, 40.0, 94.0, 120.0):
            assert air.pressure_to_spl(air.spl_to_pressure(spl)) == pytest.approx(spl)

    def test_doubling_pressure_adds_6db(self):
        assert air.pressure_to_spl(2.0) - air.pressure_to_spl(1.0) == pytest.approx(6.0206, abs=1e-3)

    def test_rejects_nonpositive_pressure(self):
        with pytest.raises(ValueError):
            air.pressure_to_spl(0.0)
