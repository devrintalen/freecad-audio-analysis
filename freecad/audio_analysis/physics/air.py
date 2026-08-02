"""Properties of humid air, in SI units throughout.

Everything a linear acoustic solve needs about the medium derives from three inputs the
user actually knows: temperature, static pressure, and relative humidity. This module
turns those into density, speed of sound, viscosity, thermal conductivity and the
boundary-layer thicknesses that decide whether a narrow slot is lossy (STRUCTURE.md 2.2).

Models used, and why they are good enough:

* **Density** -- ideal gas law for a dry-air/water-vapour mixture, with saturation vapour
  pressure from the Buck (1981) equation. Accurate to well under 0.1% across any
  condition a headphone is used in.
* **Speed of sound** -- ``c = sqrt(gamma * R * T / M)`` with molar mass and heat-capacity
  ratio both mole-fraction weighted for humidity. Exact for an ideal gas mixture; agrees
  with Cramer's (1993) reference formulation to roughly 0.1 m/s near room conditions.
* **Viscosity and thermal conductivity** -- Sutherland's law. Standard for air over the
  temperature range of interest.

Humidity has only a small effect on the speed of sound (~0.4 m/s at 20 C between dry and
saturated) but it is cheap to include and it makes measurement correlation honest.

All functions take and return SI units: kelvin, pascal, kg/m^3, m/s. Use
:func:`from_celsius` at the boundary if you have degrees Celsius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Universal gas constant, J/(mol K)
R_UNIVERSAL = 8.31446261815324

# Molar masses, kg/mol
M_DRY_AIR = 0.0289644
M_WATER = 0.01801528

# Molar isobaric heat capacities near room temperature, J/(mol K)
CP_MOLAR_DRY_AIR = 29.07
CP_MOLAR_WATER = 33.60

# Sutherland's law constants for air
MU_REF = 1.716e-5  # Pa s at T_REF
K_REF = 0.0241  # W/(m K) at T_REF
T_REF = 273.15  # K
S_MU = 110.4  # K, Sutherland constant for viscosity
S_K = 194.0  # K, Sutherland constant for thermal conductivity

# Reference conditions: 20 C, one standard atmosphere, half saturated
DEFAULT_TEMPERATURE = 293.15  # K
DEFAULT_PRESSURE = 101325.0  # Pa
DEFAULT_HUMIDITY = 0.5  # relative, 0..1

# Reference sound pressure for dB SPL, Pa
P_REF = 20e-6

ZERO_CELSIUS = 273.15


def from_celsius(temperature_c: float) -> float:
    """Degrees Celsius to kelvin."""
    return temperature_c + ZERO_CELSIUS


def to_celsius(temperature_k: float) -> float:
    """Kelvin to degrees Celsius."""
    return temperature_k - ZERO_CELSIUS


def saturation_vapour_pressure(temperature: float) -> float:
    """Saturation vapour pressure of water over liquid water, in Pa.

    Buck (1981) equation, valid roughly -30 to +60 C -- comfortably wider than any
    condition in which someone wears headphones.
    """
    t_c = to_celsius(temperature)
    return 611.21 * math.exp((18.678 - t_c / 234.5) * (t_c / (257.14 + t_c)))


def water_mole_fraction(temperature: float, pressure: float, humidity: float) -> float:
    """Mole fraction of water vapour in humid air.

    ``humidity`` is relative humidity in 0..1. Values outside that range are clamped
    rather than rejected: a user typing 50 instead of 0.5 should get plausible air, not
    an exception in the middle of a frequency sweep.
    """
    humidity = min(max(humidity, 0.0), 1.0)
    return humidity * saturation_vapour_pressure(temperature) / pressure


def molar_mass(temperature: float, pressure: float, humidity: float) -> float:
    """Mean molar mass of humid air, kg/mol.

    Water vapour is lighter than dry air, so humid air is *less* dense at fixed
    temperature and pressure -- the opposite of most people's intuition.
    """
    x_w = water_mole_fraction(temperature, pressure, humidity)
    return (1.0 - x_w) * M_DRY_AIR + x_w * M_WATER


def heat_capacity_ratio(temperature: float, pressure: float, humidity: float) -> float:
    """Ratio of specific heats, gamma = Cp/Cv, for humid air.

    Mole-fraction weighting of molar heat capacities, with ``Cv = Cp - R`` from the ideal
    gas relation.
    """
    x_w = water_mole_fraction(temperature, pressure, humidity)
    cp_molar = (1.0 - x_w) * CP_MOLAR_DRY_AIR + x_w * CP_MOLAR_WATER
    return cp_molar / (cp_molar - R_UNIVERSAL)


def density(temperature: float, pressure: float, humidity: float) -> float:
    """Density of humid air, kg/m^3."""
    return pressure * molar_mass(temperature, pressure, humidity) / (R_UNIVERSAL * temperature)


def speed_of_sound(temperature: float, pressure: float, humidity: float) -> float:
    """Speed of sound in humid air, m/s."""
    gamma = heat_capacity_ratio(temperature, pressure, humidity)
    return math.sqrt(gamma * R_UNIVERSAL * temperature / molar_mass(temperature, pressure, humidity))


def dynamic_viscosity(temperature: float) -> float:
    """Dynamic viscosity of air, Pa s, from Sutherland's law.

    Essentially independent of pressure and humidity at audio conditions.
    """
    return MU_REF * ((T_REF + S_MU) / (temperature + S_MU)) * (temperature / T_REF) ** 1.5


def thermal_conductivity(temperature: float) -> float:
    """Thermal conductivity of air, W/(m K), from a Sutherland-type law."""
    return K_REF * ((T_REF + S_K) / (temperature + S_K)) * (temperature / T_REF) ** 1.5


def specific_heat_capacity(temperature: float, pressure: float, humidity: float) -> float:
    """Isobaric specific heat capacity, J/(kg K)."""
    x_w = water_mole_fraction(temperature, pressure, humidity)
    cp_molar = (1.0 - x_w) * CP_MOLAR_DRY_AIR + x_w * CP_MOLAR_WATER
    return cp_molar / molar_mass(temperature, pressure, humidity)


def prandtl_number(temperature: float, pressure: float, humidity: float) -> float:
    """Prandtl number: the ratio of momentum to thermal diffusivity.

    Sets how much thinner the thermal boundary layer is than the viscous one. About 0.71
    for air, which is why the two layer thicknesses in STRUCTURE.md 2.2 differ by ~19%.
    """
    return (
        dynamic_viscosity(temperature)
        * specific_heat_capacity(temperature, pressure, humidity)
        / thermal_conductivity(temperature)
    )


@dataclass(frozen=True)
class AirProperties:
    """A complete, self-consistent description of the medium at one operating point.

    Construct with :meth:`at`, then hand it to physics code. Frozen so a solve cannot
    accidentally mutate the medium halfway through a frequency sweep.
    """

    temperature: float  # K
    pressure: float  # Pa
    humidity: float  # relative, 0..1
    density: float  # kg/m^3
    speed_of_sound: float  # m/s
    dynamic_viscosity: float  # Pa s
    thermal_conductivity: float  # W/(m K)
    specific_heat_capacity: float  # J/(kg K)
    prandtl_number: float  # dimensionless

    @classmethod
    def at(
        cls,
        temperature: float = DEFAULT_TEMPERATURE,
        pressure: float = DEFAULT_PRESSURE,
        humidity: float = DEFAULT_HUMIDITY,
    ) -> "AirProperties":
        """Evaluate every air property at one operating point."""
        if temperature <= 0.0:
            raise ValueError(f"temperature must be above absolute zero, got {temperature} K")
        if pressure <= 0.0:
            raise ValueError(f"pressure must be positive, got {pressure} Pa")
        return cls(
            temperature=temperature,
            pressure=pressure,
            humidity=humidity,
            density=density(temperature, pressure, humidity),
            speed_of_sound=speed_of_sound(temperature, pressure, humidity),
            dynamic_viscosity=dynamic_viscosity(temperature),
            thermal_conductivity=thermal_conductivity(temperature),
            specific_heat_capacity=specific_heat_capacity(temperature, pressure, humidity),
            prandtl_number=prandtl_number(temperature, pressure, humidity),
        )

    @property
    def characteristic_impedance(self) -> float:
        """Specific acoustic impedance of the medium, rho*c, in rayl (Pa s/m).

        About 413 rayl at room conditions. Turns up in every radiation and
        transfer-impedance expression in the workbench.
        """
        return self.density * self.speed_of_sound

    def wavelength(self, frequency: float) -> float:
        """Acoustic wavelength at ``frequency`` (Hz), in metres."""
        if frequency <= 0.0:
            raise ValueError(f"frequency must be positive, got {frequency} Hz")
        return self.speed_of_sound / frequency

    def viscous_boundary_layer(self, frequency: float) -> float:
        """Viscous boundary layer thickness, metres.

        ``delta_v = sqrt(2 mu / (rho omega))``. Roughly 70 um at 1 kHz. Any channel whose
        width approaches this is dominated by loss rather than compliance, which is what
        makes thermoviscous modelling mandatory for earphones.
        """
        if frequency <= 0.0:
            raise ValueError(f"frequency must be positive, got {frequency} Hz")
        omega = 2.0 * math.pi * frequency
        return math.sqrt(2.0 * self.dynamic_viscosity / (self.density * omega))

    def thermal_boundary_layer(self, frequency: float) -> float:
        """Thermal boundary layer thickness, metres: the viscous layer over sqrt(Pr)."""
        return self.viscous_boundary_layer(frequency) / math.sqrt(self.prandtl_number)

    def mesh_size_for(self, frequency: float, elements_per_wavelength: float = 8.0) -> float:
        """Element size, metres, needed to resolve waves at ``frequency``.

        Six to ten linear elements per wavelength is the usual rule for acoustic FEM;
        eight is a reasonable default. At 20 kHz this lands near 2 mm, which is what makes
        full-band 3D models expensive (STRUCTURE.md 2.4).
        """
        if elements_per_wavelength <= 0.0:
            raise ValueError("elements_per_wavelength must be positive")
        return self.wavelength(frequency) / elements_per_wavelength


def pressure_to_spl(pressure_pa: float) -> float:
    """RMS sound pressure in Pa to sound pressure level in dB re 20 uPa."""
    if pressure_pa <= 0.0:
        raise ValueError(f"pressure must be positive to express as SPL, got {pressure_pa} Pa")
    return 20.0 * math.log10(pressure_pa / P_REF)


def spl_to_pressure(spl_db: float) -> float:
    """Sound pressure level in dB re 20 uPa to RMS sound pressure in Pa."""
    return P_REF * 10.0 ** (spl_db / 20.0)
