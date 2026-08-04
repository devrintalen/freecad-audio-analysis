"""The Environment object -- the medium every acoustic result depends on.

Holds the three things a user actually knows (temperature, static pressure, relative
humidity) and derives everything a solver needs from them via
:mod:`freecad.audio_analysis.physics.air`. The derived values are exposed as read-only
properties so they are visible in the property editor, saved with the document, and
usable in expressions -- but cannot be edited into an inconsistent state.

Getting the medium wrong is a quiet error: a 10 C temperature offset moves the speed of
sound by ~2%, which shifts every resonance by ~2% and looks like a geometry problem.
"""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.physics import air, units
from freecad.audio_analysis.objects.base import (
    AudioObject,
    PropertySpec,
    attach_view_provider,
    is_restoring,
)

#: Frequency at which the reported boundary-layer thickness is evaluated. Shown as a
#: sanity figure for the user; the solver recomputes it per frequency.
REFERENCE_FREQUENCY = 1000.0

#: The three values a user actually knows. Everything else on the object is derived from
#: them, so a change to any of these invalidates the rest.
INPUT_PROPERTIES = ("Temperature", "StaticPressure", "RelativeHumidity")


class Environment(AudioObject):
    """Air properties for one analysis."""

    Type = "Audio::Environment"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyTemperature",
                "Temperature",
                "Medium",
                "Air temperature",
                default=FreeCAD.Units.Quantity("293.15 K"),
            ),
            PropertySpec(
                "App::PropertyPressure",
                "StaticPressure",
                "Medium",
                "Ambient static pressure",
                default=FreeCAD.Units.Quantity("101325 Pa"),
            ),
            PropertySpec(
                "App::PropertyPercent",
                "RelativeHumidity",
                "Medium",
                "Relative humidity, per cent",
                default=50,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "Density",
                "Derived",
                "Air density, kg/m^3",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "SpeedOfSound",
                "Derived",
                "Speed of sound, m/s",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "CharacteristicImpedance",
                "Derived",
                "Specific acoustic impedance rho*c, rayl",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "DynamicViscosity",
                "Derived",
                "Dynamic viscosity, Pa s",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "ThermalConductivity",
                "Derived",
                "Thermal conductivity, W/(m K)",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "PrandtlNumber",
                "Derived",
                "Prandtl number",
                read_only=True,
            ),
            PropertySpec(
                "App::PropertyFloat",
                "ViscousBoundaryLayer1kHz",
                "Derived",
                "Viscous boundary layer thickness at 1 kHz, micrometres -- channels "
                "near this width are loss-dominated",
                read_only=True,
            ),
        )

    def air_properties(self, obj: Any) -> air.AirProperties:
        """Evaluate the medium described by ``obj``, in SI units.

        This is the single conversion point from FreeCAD's internal units to SI for the
        medium. Note that FreeCAD stores pressure internally in kPa, not Pa.
        """
        return air.AirProperties.at(
            temperature=obj.Temperature.getValueAs("K").Value,
            pressure=obj.StaticPressure.getValueAs("Pa").Value,
            humidity=obj.RelativeHumidity / 100.0,
        )

    def execute(self, obj: Any) -> None:
        """Refresh the derived properties from the three user inputs."""
        props = self.air_properties(obj)
        obj.Density = props.density
        obj.SpeedOfSound = props.speed_of_sound
        obj.CharacteristicImpedance = props.characteristic_impedance
        obj.DynamicViscosity = props.dynamic_viscosity
        obj.ThermalConductivity = props.thermal_conductivity
        obj.PrandtlNumber = props.prandtl_number
        obj.ViscousBoundaryLayer1kHz = props.viscous_boundary_layer(REFERENCE_FREQUENCY) * 1.0e6

    def onChanged(self, obj: Any, prop: str) -> None:
        """Recompute derived values as soon as an input changes.

        Silent during restore. ``execute`` reads all three inputs and writes seven derived
        properties, and restore delivers them alphabetically -- ``Density`` first, then
        ``Proxy``, then ``RelativeHumidity``, ``StaticPressure``, ``Temperature`` and only
        then ``ThermalConductivity``. Recomputing partway through that sequence raised
        ``AttributeError`` three times on every document open. :meth:`on_properties_added`
        covers the case where a value genuinely needs regenerating.
        """
        if prop in INPUT_PROPERTIES and not is_restoring(obj):
            self.execute(obj)

    def on_properties_added(self, obj: Any, names: list[str]) -> None:
        """Fill in derived values for a file written before they existed."""
        if any(name not in INPUT_PROPERTIES for name in names):
            self.execute(obj)


def make_environment(doc: Any, analysis: Any = None, name: str = "Environment") -> Any:
    """Create an Environment, optionally adding it to ``analysis``."""
    obj = doc.addObject("App::FeaturePython", name)
    proxy = Environment(obj)
    proxy.execute(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.environment:ViewProviderEnvironment"
    )

    if analysis is not None:
        analysis.addObject(obj)
    return obj
