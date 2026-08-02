"""Conversions between FreeCAD's internal units and SI.

FreeCAD works internally in **millimetres**; every solver this workbench drives expects
**SI metres**. This module is the single place that conversion is allowed to happen.

The rule (see CLAUDE.md): physics code and solver input writers call these functions.
Nothing else divides by 1000. A stray factor of 1000 in an acoustic model does not crash
anything -- it produces a plausible-looking answer that is wrong, which is far worse.
"""

from __future__ import annotations

MM_PER_M = 1000.0
MM2_PER_M2 = MM_PER_M**2
MM3_PER_M3 = MM_PER_M**3
MM3_PER_LITRE = 1.0e6

# FreeCAD's base units are mm, kg, s, which makes its internal pressure unit
# kg/(mm s^2) -- that is **kilopascals**, not pascals. A property set to "101325 Pa"
# reads back as 101.325. Verified against FreeCAD 1.1.1; see tests/test_units.py.
PA_PER_INTERNAL_PRESSURE = 1000.0


def mm_to_m(value_mm: float) -> float:
    """Length: FreeCAD mm to SI metres."""
    return value_mm / MM_PER_M


def m_to_mm(value_m: float) -> float:
    """Length: SI metres to FreeCAD mm."""
    return value_m * MM_PER_M


def mm2_to_m2(value_mm2: float) -> float:
    """Area: FreeCAD mm^2 to SI m^2."""
    return value_mm2 / MM2_PER_M2


def m2_to_mm2(value_m2: float) -> float:
    """Area: SI m^2 to FreeCAD mm^2."""
    return value_m2 * MM2_PER_M2


def mm3_to_m3(value_mm3: float) -> float:
    """Volume: FreeCAD mm^3 to SI m^3."""
    return value_mm3 / MM3_PER_M3


def m3_to_mm3(value_m3: float) -> float:
    """Volume: SI m^3 to FreeCAD mm^3."""
    return value_m3 * MM3_PER_M3


def mm3_to_litre(value_mm3: float) -> float:
    """Volume: FreeCAD mm^3 to litres.

    Litres are the customary unit for loudspeaker enclosure volume, so this shows up
    in user-facing output far more than cubic metres do.
    """
    return value_mm3 / MM3_PER_LITRE


def litre_to_m3(value_litre: float) -> float:
    """Volume: litres to SI m^3."""
    return value_litre / 1000.0


def internal_pressure_to_pa(value_internal: float) -> float:
    """Pressure: FreeCAD's internal unit (kPa) to SI pascals.

    Use on the raw ``.Value`` of an ``App::PropertyPressure``. Where a ``Quantity`` is
    available, ``quantity.getValueAs("Pa")`` says the same thing more legibly and is
    preferred; this exists for the raw-float paths.
    """
    return value_internal * PA_PER_INTERNAL_PRESSURE


def pa_to_internal_pressure(value_pa: float) -> float:
    """Pressure: SI pascals to FreeCAD's internal unit (kPa)."""
    return value_pa / PA_PER_INTERNAL_PRESSURE
