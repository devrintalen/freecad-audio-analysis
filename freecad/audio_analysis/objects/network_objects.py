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

from freecad.audio_analysis.objects.base import (
    AudioObject,
    PropertySpec,
    attach_view_provider,
    is_restoring,
)

#: Property group names, kept consistent so the editor groups sensibly.
GROUP_CONNECTIONS = "Connections"
GROUP_GEOMETRY = "Geometry"
GROUP_DRIVER = "Driver"
GROUP_ELECTRICAL = "Electrical"


def quantity(value: float, unit: str) -> FreeCAD.Units.Quantity:
    return FreeCAD.Units.Quantity(f"{value} {unit}")


class NetworkObject(AudioObject):
    """Base for every object that becomes a node or an element."""

    #: Link properties naming the nodes this object connects, in order. Empty for the
    #: objects that *are* nodes. The first one that is set decides where the object sits
    #: in the tree, which is what turns a flat list into a readable adjacency list.
    NODES: tuple[str, ...] = ()

    # -- recompute ---------------------------------------------------------------------
    # Subclasses override ``update``; ``execute`` is reserved so the base can refresh the
    # tree description afterwards without every subclass having to remember to.

    def execute(self, obj: Any) -> None:
        self.update(obj)
        self.describe_connections(obj)

    def update(self, obj: Any) -> None:
        """Recompute derived values. Override this rather than :meth:`execute`."""

    # -- topology ----------------------------------------------------------------------

    def primary_node(self, obj: Any) -> Any | None:
        """The node this element is filed under in the tree.

        The first connection that is actually set. An element with every terminal on the
        exterior has no parent and stays at the top level, where being conspicuous is the
        right outcome -- it is usually a wiring mistake.
        """
        for name in self.NODES:
            node = getattr(obj, name, None)
            if node is not None:
                return node
        return None

    def connection_text(self, obj: Any) -> str:
        """``"EarCavity -> CupCavity"``, for the tree's description column."""
        if not self.NODES:
            return ""
        parts = [
            getattr(obj, name).Label if getattr(obj, name, None) is not None else "exterior"
            for name in self.NODES
        ]
        return " -> ".join(parts)

    def describe_connections(self, obj: Any) -> None:
        """Write the connection summary into ``Label2``, FreeCAD's description field.

        The tree can show it as a second column, and it is what makes a topology
        legible without clicking each object in turn. Never touched when the object has
        no connections, so a user's own note is not overwritten.
        """
        text = self.connection_text(obj)
        if text:
            obj.Label2 = text

    def onChanged(self, obj: Any, prop: str) -> None:
        """Keep neighbours' descriptions current when this object is renamed.

        FreeCAD does not treat a label change as a reason to recompute the objects that
        link to it, so without this the tree would keep showing the old name until
        something else forced a recompute. A description that quietly disagrees with the
        model is worse than no description.
        """
        if prop != "Label" or is_restoring(obj):
            return
        for dependent in getattr(obj, "InList", []):
            if is_restoring(dependent):
                continue  # Alphabetical restore order; it is not whole yet. See base.py.
            describe = getattr(getattr(dependent, "Proxy", None), "describe_connections", None)
            if not callable(describe):
                continue
            try:
                describe(dependent)
            except AttributeError:
                pass  # Backstop: a dependent may still be incomplete for other reasons.

    def area_reference_property(self, name: str = "AreaReference") -> PropertySpec:
        """A link to CAD faces whose total area drives this element's Area."""
        return PropertySpec(
            "App::PropertyLinkSubList", name, GROUP_GEOMETRY,
            "Optional CAD faces whose total area sets Area. Pick the actual opening in "
            "the model and it tracks design changes.",
        )

    def derive_area(self, obj: Any, reference: str = "AreaReference", target: str = "Area") -> None:
        """Set ``target`` from the referenced faces, if any are referenced.

        Silent when nothing is referenced -- typing a number stays perfectly valid, since
        not every acoustic element corresponds to modelled geometry.
        """
        references = getattr(obj, reference, None)
        if not references:
            return
        from freecad.audio_analysis.geometry import NoSubShapeError, referenced_area_mm2

        try:
            setattr(obj, target, quantity(referenced_area_mm2(references), "mm^2"))
        except NoSubShapeError as exc:
            FreeCAD.Console.PrintWarning(f"Audio Analysis: {obj.Label}: {exc}\n")

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
                "App::PropertyLength", "LargestDimension", "Volume",
                "Widest internal span of the cavity. Sets the frequency above which this "
                "volume stops behaving as a single compliance. Read from the linked solid "
                "when there is one; leave at zero and it is guessed from the volume, which "
                "assumes a compact shape and is therefore optimistic.",
                default=quantity(0.0, "mm"),
            ),
            PropertySpec(
                "App::PropertyString", "Description", "Volume",
                "What this cavity represents", default="",
            ),
        )

    def update(self, obj: Any) -> None:
        """Refresh the volume and the cavity's span from the referenced solid."""
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
        span = self.span_of(shape_source)
        if span:
            obj.LargestDimension = quantity(span, "mm")

    @staticmethod
    def span_of(shape_source: Any) -> float:
        """Widest bounding-box extent of a solid, in mm; 0.0 if it has no shape.

        The widest *extent* rather than the box diagonal. What matters is the distance a
        standing wave has to travel to establish itself, and for the disc-shaped cavity
        inside a headphone cup that is its diameter -- the diagonal would add the depth to
        it and understate the validity limit by a third.
        """
        shape = getattr(shape_source, "Shape", None)
        if shape is None or shape.isNull():
            return 0.0
        box = shape.BoundBox
        return float(max(box.XLength, box.YLength, box.ZLength))

    def largest_dimension_m(self, obj: Any) -> float | None:
        """The cavity span in metres, or None when it has not been established."""
        value = obj.LargestDimension.getValueAs("m").Value
        return value if value > 0.0 else None

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
    NODES = ("FrontNode", "BackNode")

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

    def update(self, obj: Any) -> None:
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
    NODES = ("NodeA", "NodeB")

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Cross-sectional area", default=quantity(2.0, "cm^2")),
            PropertySpec("App::PropertyLength", "Length", GROUP_GEOMETRY,
                         "Physical length", default=quantity(3.0, "mm")),
            PropertySpec("App::PropertyInteger", "FlangedEnds", GROUP_GEOMETRY,
                         "Number of flush-mounted ends (0, 1 or 2), for the end "
                         "correction", default=2),
            self.area_reference_property(),
            *self.node_properties("NodeA", "NodeB"),
        )

    def update(self, obj: Any) -> None:
        self.derive_area(obj)


class AcousticResistance(NetworkObject):
    """A damping mesh, felt or screen: pure dissipation.

    The primary tuning control in earphone and open-back design. Specified as a specific
    flow resistance in rayls over an area, which is how such materials are sold.

    A mesh *covering* a vent is in **series** with it: give the mesh and the vent a shared
    intermediate node. Connecting both to the same pair of nodes puts them in parallel,
    which models a second separate opening and behaves quite differently.
    """

    Type = "Audio::Resistance"
    NODES = ("NodeA", "NodeB")

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyFloat", "SpecificResistance", "Resistance",
                         "Flow resistance of the material, rayls (Pa*s/m)", default=20.0),
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Area the material covers", default=quantity(2.0, "cm^2")),
            self.area_reference_property(),
            *self.node_properties("NodeA", "NodeB"),
        )

    def update(self, obj: Any) -> None:
        self.derive_area(obj)


class LeakPath(NetworkObject):
    """A seal leak: a thin slit with both resistance and mass.

    The most influential unknown in a real headphone. Resistance goes as the inverse cube
    of the gap, so halving the gap raises it eightfold -- which is why measured bass
    response depends so heavily on fit, and why a leak belongs in every headphone model
    rather than only in pessimistic ones.
    """

    Type = "Audio::Leak"
    NODES = ("NodeA", "NodeB")

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
            PropertySpec("App::PropertyLinkSubList", "WidthReference", GROUP_GEOMETRY,
                         "Optional CAD edges whose total length sets Width -- pick the "
                         "loop where the earpad meets the head."),
            *self.node_properties("NodeA", "NodeB"),
        )

    def update(self, obj: Any) -> None:
        """Set Width from the referenced edge loop, if one is referenced."""
        references = getattr(obj, "WidthReference", None)
        if not references:
            return
        from freecad.audio_analysis.geometry import NoSubShapeError, referenced_length_mm

        try:
            obj.Width = quantity(referenced_length_mm(references), "mm")
        except NoSubShapeError as exc:
            FreeCAD.Console.PrintWarning(f"Audio Analysis: {obj.Label}: {exc}\n")


class Radiation(NetworkObject):
    """Radiation from a piston into free space.

    Terminates a node into the far field. The real part carries the radiated power, the
    imaginary part the air mass dragged along.
    """

    Type = "Audio::Radiation"
    NODES = ("NodeA",)

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec("App::PropertyArea", "Area", GROUP_GEOMETRY,
                         "Radiating area", default=quantity(26.4, "cm^2")),
            self.area_reference_property(),
            *self.node_properties("NodeA",),
        )

    def update(self, obj: Any) -> None:
        self.derive_area(obj)


class PassiveRadiator(NetworkObject):
    """A driverless diaphragm: mass and compliance, no motor."""

    Type = "Audio::PassiveRadiator"
    NODES = ("NodeA", "NodeB")

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


# ---------------------------------------------------------------------------------
# Tree shape
# ---------------------------------------------------------------------------------
#
# A lumped network is a graph, not a tree: an element joins two nodes and a node carries
# many elements, so there is no single correct parent for anything. Flattening it, which
# is what a plain group does, loses the topology entirely -- the complaint that prompted
# this. Nesting each element under the *first* node it connects loses nothing that
# matters and gains a great deal: the tree becomes an adjacency list.
#
#   EarCavity
#     Woofer    -> CupCavity
#     PadSeal   -> exterior
#   CupCavity
#     RearVent  -> BehindMesh
#
# The other end of each element is written into its description, so no information is
# hidden by the choice of parent. Every object is claimed by exactly one owner, which is
# what keeps it from appearing twice.


def owner_of(obj: Any) -> Any | None:
    """The object that should hold ``obj`` in the tree, or None for the analysis itself."""
    proxy = getattr(obj, "Proxy", None)
    primary = getattr(proxy, "primary_node", None)
    return primary(obj) if callable(primary) else None


def tree_children(obj: Any, candidates: Iterable[Any]) -> list[Any]:
    """Everything among ``candidates`` that belongs under ``obj``.

    A node claims the elements filed under it. A volume also claims the solid it measures
    itself from, so the extracted cavity sits with the acoustic object that uses it rather
    than loose in the document.
    """
    candidates = list(candidates)
    children = [
        other for other in candidates
        if other.Name != obj.Name and _same(owner_of(other), obj)
    ]
    shape = getattr(obj, "Shape", None)
    shape_name = getattr(shape, "Name", None)
    if shape_name:
        children.extend(c for c in candidates if c.Name == shape_name)
    return children


def _same(one: Any, other: Any) -> bool:
    """Whether two document-object references point at the same object.

    Compared by name rather than by identity. FreeCAD hands out a fresh Python wrapper on
    each attribute access, so ``is`` and ``id()`` are not dependable here -- two reads of
    the same link can be two objects as far as Python is concerned, and a tree built on
    that would drop children intermittently.
    """
    if one is None or other is None:
        return False
    return getattr(one, "Name", None) == getattr(other, "Name", object())


def unclaimed(members: Iterable[Any]) -> list[Any]:
    """Members of an analysis that no other member claims, for the top level."""
    members = list(members)
    claimed = {
        child.Name for member in members for child in tree_children(member, members)
    }
    return [member for member in members if member.Name not in claimed]


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
