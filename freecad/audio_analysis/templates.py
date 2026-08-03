"""Ready-made analysis topologies.

The first of the guidance mechanisms in STRUCTURE.md §6.8, and the one that matters most.
Choosing which node a driver's back connects to is the most consequential decision in a
lumped model and the least visible when it is wrong: a mistyped dimension shows up as a
shifted curve, but a mis-wired topology produces a smooth, confident answer to a different
question entirely.

So the workbench does not start from a blank canvas. Each template lays out a correct
topology with named nodes and plausible starting values; the user then supplies real
numbers. Nobody has to invent a network graph to get their first result.

Values here are sensible starting points for their device class, not measurements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import FreeCAD

from freecad.audio_analysis.objects import crossover, study
from freecad.audio_analysis.objects import network_objects as no


def q(value: float, unit: str) -> FreeCAD.Units.Quantity:
    return FreeCAD.Units.Quantity(f"{value} {unit}")


@dataclass(frozen=True)
class Template:
    """One device topology."""

    key: str
    name: str
    summary: str
    #: What the user should replace first, shown after building.
    next_steps: str
    build: Callable[[Any, Any], list[Any]]

    def apply(self, doc: Any, analysis: Any) -> list[Any]:
        created = self.build(doc, analysis)
        doc.recompute()
        return created


def _sweep_and_solver(doc, analysis, stop_hz: float) -> list[Any]:
    """Every template gets a sweep and a solver.

    The solver's LargestDimension is deliberately left at zero. Each element now supplies
    its own span -- a cavity its widest, a port its length, a diaphragm its diameter --
    and the narrowest wins, which is both more accurate and able to say *which* part of
    the model is the constraint. Setting it here would override all of that with one
    hand-typed number.
    """
    sweep = study.make_frequency_sweep(doc, analysis)
    sweep.Stop = q(stop_hz, "Hz")
    return [sweep, study.make_lumped_solver(doc, analysis)]


def _build_over_ear(doc, analysis, *, vented: bool) -> list[Any]:
    ear = no.make_volume(doc, analysis, "EarCavity")
    ear.Volume, ear.LargestDimension = q(100.0, "cm^3"), q(90.0, "mm")
    ear.Description = "Air between the earpad and the ear -- its pressure is the result"

    cup = no.make_volume(doc, analysis, "CupCavity")
    cup.Volume, cup.LargestDimension = q(200.0, "cm^3"), q(105.0, "mm")
    cup.Description = "Air behind the diaphragm, inside the cup"

    driver = no.make_driver(doc, analysis, "Driver")
    driver.FrontNode, driver.BackNode = ear, cup

    seal = no.make_leak(doc, analysis, "PadSeal")
    seal.NodeA = ear  # NodeB empty: leaks to the outside world
    created = [ear, cup, driver, seal]

    if vented:
        # Rear vent, with a mesh in series via an intermediate node. In series, not
        # parallel: all the air leaving the cup passes through both.
        behind = no.make_node(doc, analysis, "BehindMesh")
        behind.Description = "Between the rear vent and its damping mesh"
        vent = no.make_port(doc, analysis, "RearVent")
        vent.NodeA, vent.NodeB = cup, behind
        vent.Area = q(8.0, "cm^2")
        mesh = no.make_resistance(doc, analysis, "VentMesh")
        mesh.NodeA = behind  # NodeB empty: through to the outside
        mesh.Area = q(8.0, "cm^2")
        mesh.SpecificResistance = 20.0
        created += [behind, vent, mesh]

    return created + _sweep_and_solver(doc, analysis, 2000.0)


def _build_over_ear_two_way(doc, analysis) -> list[Any]:
    """Two drivers sharing one ear cavity, split by a crossover.

    The case that justifies solving the whole network at once. Both diaphragms face the
    same air, so each one's motion changes the pressure the other works against; running
    two single-driver models and adding the curves gives a different -- wrong -- answer,
    and the discrepancy is largest exactly in the crossover region where both are moving
    (STRUCTURE.md §2.4).

    The woofer's back is loaded by the cup, which vents. The tweeter gets its own small
    sealed chamber, which is what a real two-way over-ear usually does: a tweeter sharing
    the cup would be modulated by the woofer's back pressure.
    """
    ear = no.make_volume(doc, analysis, "EarCavity")
    ear.Volume, ear.LargestDimension = q(100.0, "cm^3"), q(90.0, "mm")
    ear.Description = "Air between the earpad and the ear -- its pressure is the result"

    cup = no.make_volume(doc, analysis, "CupCavity")
    cup.Volume, cup.LargestDimension = q(200.0, "cm^3"), q(105.0, "mm")
    cup.Description = "Air behind the woofer, inside the cup"

    chamber = no.make_volume(doc, analysis, "TweeterChamber")
    chamber.Volume, chamber.LargestDimension = q(3.0, "cm^3"), q(20.0, "mm")
    chamber.Description = "Sealed volume behind the tweeter, isolating it from the cup"

    woofer = no.make_driver(doc, analysis, "Woofer")
    woofer.FrontNode, woofer.BackNode = ear, cup
    woofer.Fs, woofer.Sd = q(45.0, "Hz"), q(26.4, "cm^2")
    woofer.Vas, woofer.Xmax = q(2.5, "l"), q(0.8, "mm")

    tweeter = no.make_driver(doc, analysis, "Tweeter")
    tweeter.FrontNode, tweeter.BackNode = ear, chamber
    tweeter.Fs, tweeter.Sd = q(1200.0, "Hz"), q(3.0, "cm^2")
    tweeter.Vas, tweeter.Xmax = q(0.02, "l"), q(0.2, "mm")
    tweeter.Qms, tweeter.Qes = 2.0, 0.8

    # Fourth-order Linkwitz-Riley: the branches are a full turn of phase apart at the
    # crossover, so they sum flat with both drivers wired the same way round. At second
    # order they would be half a turn apart and cancel instead.
    low = crossover.make_crossover(doc, analysis, "LowPass")
    low.Drivers, low.Response, low.Order = [woofer], "Lowpass", 4
    low.Frequency, low.NominalImpedance = q(2500.0, "Hz"), woofer.Re

    high = crossover.make_crossover(doc, analysis, "HighPass")
    high.Drivers, high.Response, high.Order = [tweeter], "Highpass", 4
    high.Frequency, high.NominalImpedance = q(2500.0, "Hz"), tweeter.Re
    # Tweeters are almost always the more sensitive of a pair, so start it padded down.
    high.Gain = -4.0

    seal = no.make_leak(doc, analysis, "PadSeal")
    seal.NodeA = ear

    behind = no.make_node(doc, analysis, "BehindMesh")
    behind.Description = "Between the rear vent and its damping mesh"
    vent = no.make_port(doc, analysis, "RearVent")
    vent.NodeA, vent.NodeB = cup, behind
    vent.Area = q(8.0, "cm^2")
    mesh = no.make_resistance(doc, analysis, "VentMesh")
    mesh.NodeA = behind
    mesh.Area, mesh.SpecificResistance = q(8.0, "cm^2"), 20.0

    created = [ear, cup, chamber, woofer, tweeter, low, high, seal, behind, vent, mesh]
    return created + _sweep_and_solver(doc, analysis, 20000.0)


def _build_in_ear(doc, analysis) -> list[Any]:
    front = no.make_volume(doc, analysis, "FrontVolume")
    front.Volume, front.LargestDimension = q(0.5, "cm^3"), q(10.0, "mm")
    front.Description = "Between the diaphragm and the nozzle"

    canal = no.make_volume(doc, analysis, "EarCanal")
    canal.Volume, canal.LargestDimension = q(1.3, "cm^3"), q(25.0, "mm")
    canal.Description = "Occluded ear volume -- its pressure is the result"

    back = no.make_volume(doc, analysis, "BackVolume")
    back.Volume, back.LargestDimension = q(0.3, "cm^3"), q(8.0, "mm")
    back.Description = "Sealed volume behind the diaphragm"

    driver = no.make_driver(doc, analysis, "Driver")
    driver.FrontNode, driver.BackNode = front, back
    driver.Fs, driver.Sd = q(700.0, "Hz"), q(0.5, "cm^2")
    driver.Vas, driver.Xmax = q(0.01, "l"), q(0.15, "mm")
    driver.Re = 16.0

    nozzle = no.make_port(doc, analysis, "Nozzle")
    nozzle.NodeA, nozzle.NodeB = front, canal
    nozzle.Area, nozzle.Length = q(3.0, "mm^2"), q(4.0, "mm")

    mesh = no.make_resistance(doc, analysis, "NozzleMesh")
    mesh.NodeA, mesh.NodeB = front, canal
    mesh.Area, mesh.SpecificResistance = q(3.0, "mm^2"), 200.0

    seal = no.make_leak(doc, analysis, "TipSeal")
    seal.NodeA = canal
    seal.Gap, seal.Width, seal.Length = q(0.05, "mm"), q(25.0, "mm"), q(3.0, "mm")

    created = [front, canal, back, driver, nozzle, mesh, seal]
    # A 1.3 cm^3 cavity stays lumped far higher than an over-ear cup does.
    return created + _sweep_and_solver(doc, analysis, 10000.0)


def _build_box(doc, analysis, *, vented: bool) -> list[Any]:
    box = no.make_volume(doc, analysis, "BoxVolume")
    box.Volume, box.LargestDimension = q(10.0, "l"), q(400.0, "mm")
    box.Description = "Enclosure interior"

    driver = no.make_driver(doc, analysis, "Driver")
    # Front node left empty: the cone radiates into the room, and the result comes from
    # far-field volume acceleration rather than a node pressure.
    driver.BackNode = box
    driver.Fs, driver.Re = q(40.0, "Hz"), 6.0
    driver.Qms, driver.Qes = 3.0, 0.5
    driver.Sd, driver.Vas = q(133.0, "cm^2"), q(10.0, "l")
    driver.Xmax, driver.Voltage = q(5.0, "mm"), q(2.83, "V")

    created = [box, driver]
    if vented:
        port = no.make_port(doc, analysis, "Port")
        port.NodeA = box  # NodeB empty: opens into the room
        port.Area, port.Length = q(20.0, "cm^2"), q(120.0, "mm")
        created.append(port)

    return created + _sweep_and_solver(doc, analysis, 500.0)


TEMPLATES: tuple[Template, ...] = (
    Template(
        key="over_ear_open",
        name="Over-ear headphone, open back",
        summary=(
            "Driver between an ear cavity and a cup cavity, with a pad seal leaking to "
            "the outside and a rear vent damped by a mesh in series."
        ),
        next_steps=(
            "Replace the Driver's Thiele-Small parameters with real ones, set EarCavity "
            "and CupCavity from your CAD, then sweep RearVent Area and VentMesh "
            "SpecificResistance to see what the rear opening does."
        ),
        build=lambda doc, analysis: _build_over_ear(doc, analysis, vented=True),
    ),
    Template(
        key="over_ear_closed",
        name="Over-ear headphone, closed back",
        summary="Driver between an ear cavity and a sealed cup, with a pad seal leak.",
        next_steps=(
            "Replace the Driver parameters and cavity volumes. Compare against the "
            "open-back template to see what sealing the cup costs and gains."
        ),
        build=lambda doc, analysis: _build_over_ear(doc, analysis, vented=False),
    ),
    Template(
        key="over_ear_two_way",
        name="Over-ear headphone, two-way",
        summary=(
            "A woofer and a tweeter facing one shared ear cavity, split by a Linkwitz-"
            "Riley crossover. The woofer's back vents through the cup; the tweeter has "
            "its own sealed chamber."
        ),
        next_steps=(
            "Set both drivers' parameters and the cavity volumes, then tune the crossover "
            "frequency and the tweeter's Gain. Note the validity warning: a 2.5 kHz "
            "crossover is far above where a lumped model of a 105 mm cup can be trusted, "
            "so use this to compare candidates rather than to predict the summed curve."
        ),
        build=lambda doc, analysis: _build_over_ear_two_way(doc, analysis),
    ),
    Template(
        key="in_ear",
        name="In-ear monitor",
        summary=(
            "Driver with a sealed back volume feeding an occluded ear canal through a "
            "damped nozzle, with a tip seal leak."
        ),
        next_steps=(
            "Set the driver parameters and the nozzle geometry. NozzleMesh resistance is "
            "the main tuning control; TipSeal Gap shows how much fit matters."
        ),
        build=lambda doc, analysis: _build_in_ear(doc, analysis),
    ),
    Template(
        key="sealed_box",
        name="Loudspeaker, sealed box",
        summary="Driver radiating into the room, its rear loaded by a sealed enclosure.",
        next_steps=(
            "Set the driver parameters and box volume. The front node is deliberately "
            "empty -- the cone radiates into the room, so read the result from far-field "
            "pressure rather than a node."
        ),
        build=lambda doc, analysis: _build_box(doc, analysis, vented=False),
    ),
    Template(
        key="vented_box",
        name="Loudspeaker, vented box",
        summary="Sealed box plus a tuned port, both radiating into the room.",
        next_steps=(
            "Set the driver parameters, box volume and port dimensions. Port length and "
            "area set the tuning frequency; watch for a second impedance peak."
        ),
        build=lambda doc, analysis: _build_box(doc, analysis, vented=True),
    ),
)

TEMPLATES_BY_KEY = {template.key: template for template in TEMPLATES}


def apply_template(key: str, doc: Any, analysis: Any) -> Template:
    """Build ``key``'s topology inside ``analysis`` and return the template."""
    template = TEMPLATES_BY_KEY.get(key)
    if template is None:
        raise KeyError(f"unknown template {key!r}; known: {sorted(TEMPLATES_BY_KEY)}")
    template.apply(doc, analysis)
    return template
