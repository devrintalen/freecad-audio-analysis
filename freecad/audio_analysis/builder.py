"""Translate an analysis's document objects into a solvable physics network.

The seam between the FreeCAD layer and the physics layer. Everything above this module
deals in document objects, links and FreeCAD units; everything below deals in SI floats
and NumPy arrays. Keeping the boundary in one place is what lets the physics be tested
without FreeCAD, and what keeps unit conversion auditable.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from freecad.audio_analysis.objects.base import is_audio_object
from freecad.audio_analysis.objects.network_objects import (
    NODE_TYPES,
    AcousticNode,
    AcousticResistance,
    AcousticVolume,
    Driver,
    LeakPath,
    PassiveRadiator,
    Port,
    Radiation,
)
from freecad.audio_analysis.physics import air, network as net_physics


class BuildError(ValueError):
    """Raised when an analysis cannot be turned into a solvable network."""


def _members(analysis: Any) -> list[Any]:
    return list(getattr(analysis, "Group", []) or [])


def _of_type(analysis: Any, type_name: str) -> list[Any]:
    return [o for o in _members(analysis) if is_audio_object(o, type_name)]


def medium_of(analysis: Any) -> air.AirProperties:
    """Air properties from the analysis's Environment, or room conditions if absent."""
    from freecad.audio_analysis.objects.environment import Environment

    environments = _of_type(analysis, Environment.Type)
    if not environments:
        return air.AirProperties.at()
    return environments[0].Proxy.air_properties(environments[0])


def node_name(link: Any) -> str:
    """Network node name for a link, with an empty link meaning the exterior.

    An unset node is ambient pressure. That is the right default for a vent or a radiating
    face, and it is what makes an open-back design expressible without extra objects.
    """
    if link is None:
        return net_physics.GROUND
    return link.Name


def sweep_frequencies(analysis: Any) -> np.ndarray:
    """Frequencies from the analysis's FrequencySweep, or a default audio-band sweep."""
    from freecad.audio_analysis.objects.study import FrequencySweep
    from freecad.audio_analysis.results.curve import log_frequencies

    sweeps = _of_type(analysis, FrequencySweep.Type)
    if not sweeps:
        return log_frequencies(20.0, 20000.0, 24)
    if len(sweeps) > 1:
        raise BuildError(
            f"this analysis has {len(sweeps)} frequency sweeps; it needs exactly one"
        )
    return sweeps[0].Proxy.frequencies(sweeps[0])


def filter_for(analysis: Any, driver: Any):
    """The crossover branch feeding ``driver``, as a physics filter, or None.

    A driver with no crossover is driven straight from the amplifier, which is the right
    default for a single-driver design and keeps every existing model unchanged.
    """
    from freecad.audio_analysis.objects.crossover import crossover_for
    from freecad.audio_analysis.physics.crossover import CrossoverError

    branch = crossover_for(analysis, driver)
    if branch is None:
        return None
    try:
        return branch.Proxy.filter(branch)
    except CrossoverError as exc:
        raise BuildError(f"{branch.Label}: {exc}") from exc


def _length(obj: Any, name: str) -> float:
    return getattr(obj, name).getValueAs("m").Value


def _area(obj: Any, name: str) -> float:
    return getattr(obj, name).getValueAs("m^2").Value


def build_network(analysis: Any) -> tuple[net_physics.Network, air.AirProperties]:
    """Assemble the physics network described by ``analysis``.

    Returns the network and the medium it was built with. Raises :class:`BuildError` with
    a message naming the offending object when an element cannot be built, rather than
    letting a unit or link problem surface later as a singular matrix.
    """
    medium = medium_of(analysis)
    network = net_physics.Network(medium)

    volumes = _of_type(analysis, AcousticVolume.Type)
    drivers = _of_type(analysis, Driver.Type)

    if not drivers:
        raise BuildError(
            "this analysis has no Driver, so there is nothing to make sound. Add one "
            "and connect its front and back nodes."
        )

    # Volumes are nodes that also carry a compliance to ambient.
    for volume in volumes:
        value = volume.Volume.getValueAs("m^3").Value
        if value <= 0.0:
            raise BuildError(f"{volume.Label}: volume must be positive, got {value} m^3")
        network.add(
            net_physics.Compliance(f"{volume.Name}_compliance", value, volume.Name)
        )

    for driver in drivers:
        try:
            parameters = driver.Proxy.parameters(driver, medium)
        except ValueError as exc:
            raise BuildError(f"{driver.Label}: {exc}") from exc
        network.add(
            net_physics.Driver(
                driver.Name,
                parameters,
                front_node=node_name(driver.FrontNode),
                back_node=node_name(driver.BackNode),
                voltage=driver.Voltage.getValueAs("V").Value,
                polarity=-1 if driver.Inverted else 1,
                source_impedance=driver.SourceImpedance,
                filter=filter_for(analysis, driver),
            )
        )

    for port in _of_type(analysis, Port.Type):
        network.add(
            net_physics.AcousticMass(
                port.Name,
                area=_area(port, "Area"),
                length=_length(port, "Length"),
                node_a=node_name(port.NodeA),
                node_b=node_name(port.NodeB),
                flanged_ends=port.FlangedEnds,
            )
        )

    for resistance in _of_type(analysis, AcousticResistance.Type):
        network.add(
            net_physics.Resistance.from_rayls(
                resistance.Name,
                specific_resistance=resistance.SpecificResistance,
                area=_area(resistance, "Area"),
                node_a=node_name(resistance.NodeA),
                node_b=node_name(resistance.NodeB),
            )
        )

    for leak in _of_type(analysis, LeakPath.Type):
        network.add(
            net_physics.Leak(
                leak.Name,
                gap=_length(leak, "Gap"),
                width=_length(leak, "Width"),
                length=_length(leak, "Length"),
                node_a=node_name(leak.NodeA),
                node_b=node_name(leak.NodeB),
            )
        )

    for radiation in _of_type(analysis, Radiation.Type):
        network.add(
            net_physics.PistonRadiation(
                radiation.Name,
                area=_area(radiation, "Area"),
                node_a=node_name(radiation.NodeA),
            )
        )

    for pr in _of_type(analysis, PassiveRadiator.Type):
        network.add(
            net_physics.PassiveRadiator(
                pr.Name,
                mass=pr.Mass,
                compliance=pr.Compliance,
                area=_area(pr, "Area"),
                resistance=pr.Resistance,
                node_a=node_name(pr.NodeA),
                node_b=node_name(pr.NodeB),
            )
        )

    return network, medium


def node_objects(analysis: Any) -> list[Any]:
    """Every object in the analysis that acts as a network node."""
    return [o for o in _members(analysis) if any(is_audio_object(o, t) for t in NODE_TYPES)]


def label_for_node(analysis: Any, name: str) -> str:
    """Human-readable label for a network node name."""
    if name == net_physics.GROUND:
        return "Exterior"
    for obj in node_objects(analysis):
        if obj.Name == name:
            return obj.Label
    return name
