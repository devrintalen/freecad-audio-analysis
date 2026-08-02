"""Document objects for the lumped network (STRUCTURE.md §6.6).

Each object here is the user-facing form of one element in
:mod:`freecad.audio_analysis.physics.network`. The physics lives there; these carry the
properties, the units, and the links that describe topology.

**Topology is explicit.** Every element names the nodes it connects. A node link left
empty means the **exterior** — ambient pressure, the far field — which is the physically
right default for a vent or a radiating face, and is what makes an open-back design
expressible without ceremony. It is also the one place a user can silently mean something
they did not, so the Tier 1 checks call it out where it looks unintentional.

**Units.** Every quantity property is converted with ``getValueAs`` rather than its raw
``Value``. FreeCAD's internal units are not what you would guess — pressure is stored in
kilopascals and electric potential in microvolts — so raw values are never safe.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import FreeCAD

from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider

#: Property group names, kept consistent so the editor groups sensibly.
GROUP_CONNECTIONS = "Connections"
GROUP_GEOMETRY = "Geometry"
GROUP_DRIVER = "Driver"
GROUP_ELECTRICAL = "Electrical"


def quantity(value: float, unit: str) -> FreeCAD.Units.Quantity:
    return FreeCAD.Units.Quantity(f"{value} {unit}")


class NetworkObject(AudioObject):
    """Base for every object that becomes a node or an element."""

    def node_properties(self, *names: str) -> tuple[PropertySpec, ...]:
        """Link properties for the nodes this element connects."""
        return tuple(
            PropertySpec(
                "App::PropertyLink",
                name,
                GROUP_CONNECTIONS,
                "Node this terminal connects to. Leave empty for the exterior "
                "(ambient pressure).",
            )
            for name in names
        )


# ---------------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------------


class AcousticNode(NetworkObject):
    """A junction with no volume of its own.

    Needed where three or more elements meet, or to place a damping mesh in series with a
    vent -- the mesh and the vent share an intermediate node. Wiring such a mesh in
    parallel instead is a real and easy mistake: it models a second, separate opening.
    """

    Type = "Audio::Node"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyString", "Description", "Node",
                "What this junction represents", default="",
            ),
        )


class AcousticVolume(NetworkObject):
    """An enclosed volume of air: a node that is also a compliance.

    A volume behaves as a spring, ``Ca = V/(rho c^2)``. Smaller means stiffer, which is
    why a small sealed cup raises a driver's resonance.

    The volume can be read straight from a CAD solid. That is the point of living inside
    FreeCAD -- but remember it is the *air* that matters, not the part, so the referenced
    solid must be the cavity (§6.5).
    """

    Type = "Audio::Volume"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyVolume", "Volume", "Volume",
                "Enclosed air volume", default=quantity(100.0, "cm^3"),
            ),
            PropertySpec(
                "App::PropertyLink", "Shape", "Volume",
                "Optional solid representing the air. When set, Volume is measured from "
                "it on every recompute.",
            ),
            PropertySpec(
                "App::PropertyString", "Description", "Volume",
                "What this cavity represents", default="",
            ),
        )

    def execute(self, obj: Any) -> None:
        """Refresh the volume from the referenced solid, if there is one."""
        shape_source = getattr(obj, "Shape", None)
        if shape_source is None:
            return
        from freecad.audio_analysis.geometry import NoSolidError, measure_volume

        try:
            measurement = measure_volume(shape_source)
        except NoSolidError as exc:
            FreeCAD.Console.PrintWarning(f"Audio Analysis: {obj.Label}: {exc}\n")
            return
        obj.Volume = quantity(measurement.volume_mm3, "mm^3")

    def volume_m3(self, obj: Any) -> float:
        return obj.Volume.getValueAs("m^3").Value


# ---------------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------------


class Driver(NetworkObject):
    """A moving-coil driver, described by its Thiele-Small parameters.

    ``FrontNode`` is where the diaphragm radiates and ``BackNode`` is what loads its rear.
    Both being explicit is what lets one object serve a sealed box, a vented box, an open
    back, or two drivers sharing a chamber (§6.6). Several Drivers per analysis is the
    normal case, not an advanced one.
    """

    Type = "Audio::Driver"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyFrequency", "Fs", GROUP_DRIVER,
                         "Free-air resonance", default=quantity(45.0, "Hz")),
            PropertySpec("App::PropertyFloat", "Re", GROUP_ELECTRICAL,
                         "Voice coil DC resistance, ohms", default=32.0),
            PropertySpec("App::PropertyFloat", "Le", GROUP_ELECTRICAL,
                         "Voice coil inductance, henries", default=0.0),
            PropertySpec("App::PropertyFloat", "Qms", GROUP_DRIVER,
                         "Mechanical Q at resonance", default=2.5),
            PropertySpec("App::PropertyFloat", "Qes", GROUP_DRIVER,
                         "Electrical Q at resonance", default=0.7),
            PropertySpec("App::PropertyArea", "Sd", GROUP_DRIVER,
                         "Effective radiating area", default=quantity(26.4, "cm^2")),
            PropertySpec("App::PropertyVolume", "Vas", GROUP_DRIVER,
                         "Equivalent suspension volume", default=quantity(2.5, "l")),
            PropertySpec("App::PropertyLength", "Xmax", GROUP_DRIVER,
                         "Maximum linear excursion, one-way peak",
                         default=quantity(0.8, "mm")),
            PropertySpec("App::PropertyElectricPotential", "Voltage", GROUP_ELECTRICAL,
                         "Drive voltage, RMS", default=quantity(0.1, "V")),
            PropertySpec("App::PropertyFloat", "SourceImpedance", GROUP_ELECTRICAL,
                         "Amplifier output impedance, ohms", default=0.0),
            PropertySpec("App::PropertyBool", "Inverted", GROUP_ELECTRICAL,
                         "Reverse polarity. Through a crossover region this changes the "
                         "summed response by tens of dB.", default=False),
            *self.node_properties("FrontNode", "BackNode"),
            PropertySpec("App::PropertyFloat", "Qts", "Derived",
                         "Total Q at resonance", read_only=True),
        )

    def parameters(self, obj: Any, medium: Any = None):
        """Build the physics-layer :class:`DriverParameters` from this object."""
        from freecad.audio_analysis.physics.driver import DriverParameters

        return DriverParameters.from_thiele_small(
            name=obj.Label,
            fs=obj.Fs.getValueAs("Hz").Value,
            Re=obj.Re,
            Qms=obj.Qms,
            Qes=obj.Qes,
            Sd=obj.Sd.getValueAs("m^2").Value,
            Vas=obj.Vas.getValueAs("m^3").Value,
            Le=obj.Le,
            Xmax=obj.Xmax.getValueAs("m").Value,
            medium=medium,
        )

    def execute(self, obj: Any) -> None:
        try:
            obj.Qts = self.parameters(obj).Qts
        except ValueError:
            pass  # Half-entered parameters during editing; the checks will report it.


class Port(NetworkObject):
    """A duct, vent or opening: a slug of air with mass.

    The effective length exceeds the physical length because air just outside the opening
    moves with it. That end correction is applied automatically.
    """

    Type = "Audio::Port"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Cross-sectional area", default=quantity(2.0, "cm^2")),
            PropertySpec("App::PropertyLength", "Length", GROUP_GEOMETRY,
                         "Physical length", default=quantity(3.0, "mm")),
            PropertySpec("App::PropertyInteger", "FlangedEnds", GROUP_GEOMETRY,
                         "Number of flush-mounted ends (0, 1 or 2), for the end "
                         "correction", default=2),
            *self.node_properties("NodeA", "NodeB"),
        )


class AcousticResistance(NetworkObject):
    """A damping mesh, felt or screen: pure dissipation.

    The primary tuning control in earphone and open-back design. Specified as a specific
    flow resistance in rayls over an area, which is how such materials are sold.

    A mesh *covering* a vent is in **series** with it: give the mesh and the vent a shared
    intermediate node. Connecting both to the same pair of nodes puts them in parallel,
    which models a second separate opening and behaves quite differently.
    """

    Type = "Audio::Resistance"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyFloat", "SpecificResistance", "Resistance",
                         "Flow resistance of the material, rayls (Pa*s/m)", default=20.0),
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Area the material covers", default=quantity(2.0, "cm^2")),
            *self.node_properties("NodeA", "NodeB"),
        )


class LeakPath(NetworkObject):
    """A seal leak: a thin slit with both resistance and mass.

    The most influential unknown in a real headphone. Resistance goes as the inverse cube
    of the gap, so halving the gap raises it eightfold -- which is why measured bass
    response depends so heavily on fit, and why a leak belongs in every headphone model
    rather than only in pessimistic ones.
    """

    Type = "Audio::Leak"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyLength", "Gap", GROUP_GEOMETRY,
                         "Slit height", default=quantity(0.15, "mm")),
            PropertySpec("App::PropertyLength", "Width", GROUP_GEOMETRY,
                         "Slit width, e.g. the perimeter of an earpad",
                         default=quantity(350.0, "mm")),
            PropertySpec("App::PropertyLength", "Length", GROUP_GEOMETRY,
                         "Depth of the leak path along the flow direction",
                         default=quantity(4.0, "mm")),
            *self.node_properties("NodeA", "NodeB"),
        )


class Radiation(NetworkObject):
    """Radiation from a piston into free space.

    Terminates a node into the far field. The real part carries the radiated power, the
    imaginary part the air mass dragged along.
    """

    Type = "Audio::Radiation"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Radiating area", default=quantity(26.4, "cm^2")),
            *self.node_properties("NodeA",),
        )


class PassiveRadiator(NetworkObject):
    """A driverless diaphragm: mass and compliance, no motor."""

    Type = "Audio::PassiveRadiator"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyFloat", "Mass", "PassiveRadiator",
                         "Moving mass, kg", default=0.02),
            PropertySpec("App::PropertyFloat", "Compliance", "PassiveRadiator",
                         "Suspension compliance, m/N", default=5.0e-4),
            PropertySpec("App::PropertyFloat", "Resistance", "PassiveRadiator",
                         "Mechanical loss, N*s/m", default=0.5),
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Effective radiating area", default=quantity(20.0, "cm^2")),
            *self.node_properties("NodeA", "NodeB"),
        )


# ---------------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------------

#: Proxy class for each object type, used by the factory and by the network builder.
NETWORK_CLASSES: tuple[type[NetworkObject], ...] = (
    AcousticNode,
    AcousticVolume,
    Driver,
    Port,
    AcousticResistance,
    LeakPath,
    Radiation,
    PassiveRadiator,
)

#: Objects that act as nodes rather than elements.
NODE_TYPES = (AcousticNode.Type, AcousticVolume.Type)


def _make(doc: Any, proxy_class: type[NetworkObject], name: str, analysis: Any = None) -> Any:
    obj = doc.addObject("App::FeaturePython", name)
    proxy = proxy_class(obj)
    proxy.execute(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.network:ViewProviderNetworkObject"
    )
    if analysis is not None:
        analysis.addObject(obj)
    return obj


def make_node(doc: Any, analysis: Any = None, name: str = "Node") -> Any:
    return _make(doc, AcousticNode, name, analysis)


def make_volume(doc: Any, analysis: Any = None, name: str = "Volume") -> Any:
    return _make(doc, AcousticVolume, name, analysis)


def make_driver(doc: Any, analysis: Any = None, name: str = "Driver") -> Any:
    return _make(doc, Driver, name, analysis)


def make_port(doc: Any, analysis: Any = None, name: str = "Port") -> Any:
    return _make(doc, Port, name, analysis)


def make_resistance(doc: Any, analysis: Any = None, name: str = "Resistance") -> Any:
    return _make(doc, AcousticResistance, name, analysis)


def make_leak(doc: Any, analysis: Any = None, name: str = "Leak") -> Any:
    return _make(doc, LeakPath, name, analysis)


def make_radiation(doc: Any, analysis: Any = None, name: str = "Radiation") -> Any:
    return _make(doc, Radiation, name, analysis)


def make_passive_radiator(doc: Any, analysis: Any = None, name: str = "PassiveRadiator") -> Any:
    return _make(doc, PassiveRadiator, name, analysis)
