"""Lumped acoustic network solver.

The Tier 1 engine. Builds a nodal admittance matrix from an arbitrary topology of
acoustic elements and solves it at every frequency at once.

**The analogy.** Acoustic elements map onto circuit elements: pressure plays the role of
voltage, volume velocity the role of current, and acoustic impedance ``Z = p/U`` the role
of electrical impedance. A sealed volume is a capacitor, a port is an inductor, a damping
mesh is a resistor. That correspondence is exact for the linear, small-signal, long-
wavelength regime this tier covers.

**Why a general solver.** Multiple drivers are a first-class requirement (STRUCTURE.md
§2.4), and drivers sharing a volume load each other, so they must be solved
simultaneously. A nodal formulation handles one driver or five with the same code, and
acoustic summation falls out of the solve with correct phase rather than being bolted on.

**Nodes.** A node is a region of air at a single pressure. :data:`GROUND` is the reference
at ambient pressure — the far field, and the other side of every enclosed volume's
compliance.

**Vectorised.** Every element returns its impedance as an array over all frequencies, and
the whole sweep is solved as one batched linear solve. A few hundred frequencies over a
few tens of nodes takes milliseconds, which is what makes parameter sweeps interactive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from scipy import special

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.crossover import Filter
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.results.curve import ResponseCurve

#: The reference node, at ambient pressure. Enclosed volumes and radiation terminate here.
GROUND = "GROUND"


class Element:
    """Base class: a two-terminal acoustic element between two nodes."""

    def __init__(self, name: str, node_a: str, node_b: str = GROUND) -> None:
        if not name:
            raise ValueError("elements need a name")
        if node_a == node_b:
            raise ValueError(f"{name}: both terminals are connected to {node_a!r}")
        self.name = name
        self.node_a = node_a
        self.node_b = node_b

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        """Acoustic impedance in Pa*s/m^3, one complex value per frequency."""
        raise NotImplementedError

    def source(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray | None:
        """Norton-equivalent volume-velocity source injected from ``node_b`` to ``node_a``.

        None for passive elements.
        """
        return None

    @property
    def nodes(self) -> tuple[str, str]:
        return (self.node_a, self.node_b)


class Compliance(Element):
    """An enclosed volume of air acting as a spring.

    ``Ca = V / (rho c^2)``, and ``Z = 1/(j omega Ca)``. Smaller volume, stiffer spring,
    higher impedance at low frequency — which is why a small sealed box raises a driver's
    resonance.
    """

    def __init__(self, name: str, volume: float, node_a: str, node_b: str = GROUND) -> None:
        super().__init__(name, node_a, node_b)
        if volume <= 0.0:
            raise ValueError(f"{name}: volume must be positive, got {volume} m^3")
        self.volume = volume

    def compliance(self, medium: air.AirProperties) -> float:
        return self.volume / (medium.density * medium.speed_of_sound**2)

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        return 1.0 / (1j * omega * self.compliance(medium))


class AcousticMass(Element):
    """A slug of air in a duct, port or vent.

    ``Ma = rho * L_eff / S``, and ``Z = j omega Ma``. The effective length exceeds the
    physical length because air just outside the opening moves with it; that end
    correction is added here rather than left to the user.
    """

    #: End correction per open end, as a multiple of the radius. 0.85 is the standard
    #: flanged value; an unflanged end is nearer 0.61.
    FLANGED_CORRECTION = 0.85
    UNFLANGED_CORRECTION = 0.61

    def __init__(
        self,
        name: str,
        area: float,
        length: float,
        node_a: str,
        node_b: str = GROUND,
        *,
        flanged_ends: int = 2,
    ) -> None:
        super().__init__(name, node_a, node_b)
        if area <= 0.0:
            raise ValueError(f"{name}: area must be positive, got {area} m^2")
        if length < 0.0:
            raise ValueError(f"{name}: length must not be negative, got {length} m")
        if not 0 <= flanged_ends <= 2:
            raise ValueError(f"{name}: flanged_ends must be 0, 1 or 2")
        self.area = area
        self.length = length
        self.flanged_ends = flanged_ends

    @property
    def radius(self) -> float:
        return math.sqrt(self.area / math.pi)

    @property
    def effective_length(self) -> float:
        """Physical length plus an end correction for each open end."""
        return self.length + self.flanged_ends * self.FLANGED_CORRECTION * self.radius

    def mass(self, medium: air.AirProperties) -> float:
        return medium.density * self.effective_length / self.area

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        return 1j * omega * self.mass(medium)


class Resistance(Element):
    """A purely dissipative element: damping mesh, felt, or a deliberately lossy vent.

    Specified either as an acoustic resistance directly (Pa*s/m^3) or, more naturally for
    a mesh, as a specific flow resistance in rayls over an area.
    """

    def __init__(
        self,
        name: str,
        resistance: float,
        node_a: str,
        node_b: str = GROUND,
    ) -> None:
        super().__init__(name, node_a, node_b)
        if resistance <= 0.0:
            raise ValueError(f"{name}: resistance must be positive, got {resistance}")
        self.resistance = resistance

    @classmethod
    def from_rayls(
        cls, name: str, specific_resistance: float, area: float, node_a: str, node_b: str = GROUND
    ) -> "Resistance":
        """Build from a mesh's specific flow resistance in rayls (Pa*s/m) over ``area``.

        This is how damping materials are actually specified, so it saves the user a
        division they can get wrong.
        """
        if area <= 0.0:
            raise ValueError(f"{name}: area must be positive, got {area} m^2")
        return cls(name, specific_resistance / area, node_a, node_b)

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        return np.full(omega.shape, self.resistance, dtype=complex)


class Leak(Element):
    """A seal leak: a narrow slit with both mass and resistance.

    The single most influential unknown in a real headphone (§2.2). Modelled as a short
    duct whose viscous losses are computed from the gap and the boundary-layer thickness,
    so the resistance falls out of the geometry rather than being invented.

    For a slit of width ``w``, gap ``h`` and length ``l``, the low-frequency viscous
    resistance is ``12 mu l / (w h^3)`` — the parallel-plate Poiseuille result. The cubic
    dependence on gap is why seal quality dominates: halving the gap raises the resistance
    eightfold.
    """

    def __init__(
        self,
        name: str,
        gap: float,
        width: float,
        length: float,
        node_a: str,
        node_b: str = GROUND,
    ) -> None:
        super().__init__(name, node_a, node_b)
        for value, label in ((gap, "gap"), (width, "width"), (length, "length")):
            if value <= 0.0:
                raise ValueError(f"{name}: {label} must be positive, got {value} m")
        self.gap = gap
        self.width = width
        self.length = length

    @property
    def area(self) -> float:
        return self.gap * self.width

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        resistance = 12.0 * medium.dynamic_viscosity * self.length / (self.width * self.gap**3)
        # The mass term uses 6/5 of the geometric mass, the standard correction for the
        # parabolic velocity profile in a slit.
        mass = 1.2 * medium.density * self.length / self.area
        return resistance + 1j * omega * mass


class PistonRadiation(Element):
    """Radiation impedance of a circular piston in an infinite baffle.

    ``Z = (rho c / S) [1 - 2 J1(2ka)/(2ka) + j 2 H1(2ka)/(2ka)]`` with ``J1`` the Bessel
    function and ``H1`` the Struve function. The real part is the radiated power; the
    imaginary part is the air mass carried along, which lowers the resonance slightly.

    At low frequency this tends to ``(rho c/S)[(ka)^2/2 + j 8ka/(3 pi)]`` — radiation
    resistance rising as frequency squared, which is why small sources are poor radiators
    in the bass.
    """

    def __init__(self, name: str, area: float, node_a: str, node_b: str = GROUND) -> None:
        super().__init__(name, node_a, node_b)
        if area <= 0.0:
            raise ValueError(f"{name}: area must be positive, got {area} m^2")
        self.area = area

    @property
    def radius(self) -> float:
        return math.sqrt(self.area / math.pi)

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        k = omega / medium.speed_of_sound
        x = 2.0 * k * self.radius
        # Both terms are 0/0 at DC; the limits are 0, so substitute a tiny x and let the
        # series behaviour take over rather than dividing by zero.
        x = np.maximum(x, 1e-12)
        real = 1.0 - 2.0 * special.j1(x) / x
        imag = 2.0 * special.struve(1, x) / x
        return (medium.density * medium.speed_of_sound / self.area) * (real + 1j * imag)


class Driver(Element):
    """A moving-coil driver, as a Thévenin source between its back and front nodes.

    The derivation, in the acoustical domain. With ``Ze = Re + j omega Le + Zs`` the total
    electrical impedance and ``u`` the volume velocity leaving the front face:

        p_front - p_back = p_source - Z_driver * u

        p_source  = BL * V / (Ze * Sd)
        Z_driver  = (j omega Mms + Rms + 1/(j omega Cms)) / Sd^2  +  BL^2 / (Sd^2 Ze)

    The second term of ``Z_driver`` is the motor's electrical damping reflected into the
    acoustical domain: it is why shorting a driver's terminals stiffens its cone.

    Stamped as its Norton equivalent — a volume-velocity source in parallel with an
    admittance — so the whole network stays a plain nodal solve with no extra unknowns.

    ``front_node`` is where the diaphragm radiates; ``back_node`` is what loads its rear.
    Having both as explicit nodes is what lets one formulation express a sealed box, a
    vented box, an open back, or two drivers sharing a chamber.

    An optional ``filter`` is the crossover branch feeding this driver. It changes both
    the voltage arriving at the coil and the impedance looking back at the amplifier, so
    ``voltage`` and ``source_impedance`` become curves rather than numbers — see
    :mod:`freecad.audio_analysis.physics.crossover`. Nothing else in the solve changes.
    """

    def __init__(
        self,
        name: str,
        parameters: DriverParameters,
        front_node: str,
        back_node: str = GROUND,
        *,
        voltage: float = 2.83,
        polarity: int = 1,
        source_impedance: float = 0.0,
        filter: "Filter | None" = None,
    ) -> None:
        super().__init__(name, front_node, back_node)
        if polarity not in (1, -1):
            raise ValueError(f"{name}: polarity must be +1 or -1, got {polarity}")
        if source_impedance < 0.0:
            raise ValueError(f"{name}: source impedance must not be negative")
        self.parameters = parameters
        self.voltage = voltage
        self.polarity = polarity
        self.source_impedance = source_impedance
        self.filter = filter

    @property
    def front_node(self) -> str:
        return self.node_a

    @property
    def back_node(self) -> str:
        return self.node_b

    def drive(self, omega: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(terminal voltage, impedance looking back)`` at the voice coil.

        The Thévenin equivalent of the amplifier and any crossover ahead of it. Both are
        independent of the driver, so this is computable before the acoustic solve — which
        is what keeps a crossover from making the problem circular.
        """
        if self.filter is None:
            ones = np.ones(np.shape(omega), dtype=complex)
            return self.voltage * ones, self.source_impedance * ones
        gain, back = self.filter.thevenin(omega, self.source_impedance)
        return self.voltage * gain, back

    def electrical_impedance(self, omega: np.ndarray) -> np.ndarray:
        """Voice coil impedance plus whatever the coil sees looking back."""
        p = self.parameters
        return p.Re + 1j * omega * p.Le + self.drive(omega)[1]

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        p = self.parameters
        mechanical = (1j * omega * p.Mms + p.Rms + 1.0 / (1j * omega * p.Cms)) / p.Sd**2
        electrical = p.BL**2 / (p.Sd**2 * self.electrical_impedance(omega))
        return mechanical + electrical

    def open_circuit_pressure(self, omega: np.ndarray) -> np.ndarray:
        """The Thévenin pressure the motor develops, before acoustic loading."""
        p = self.parameters
        terminal = self.drive(omega)[0]
        return self.polarity * p.BL * terminal / (self.electrical_impedance(omega) * p.Sd)

    def source(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        return self.open_circuit_pressure(omega) / self.impedance(omega, medium)


class PassiveRadiator(Element):
    """A driverless diaphragm: mass and compliance, no motor.

    Used in place of a port when a port would be impractically long, and the standard way
    to extend bass in a shallow enclosure. Behaves as a series resonant branch.
    """

    def __init__(
        self,
        name: str,
        *,
        mass: float,
        compliance: float,
        area: float,
        resistance: float = 0.0,
        node_a: str,
        node_b: str = GROUND,
    ) -> None:
        super().__init__(name, node_a, node_b)
        for value, label in ((mass, "mass"), (compliance, "compliance"), (area, "area")):
            if value <= 0.0:
                raise ValueError(f"{name}: {label} must be positive, got {value}")
        self.mass = mass
        self.compliance = compliance
        self.area = area
        self.resistance = resistance

    @property
    def resonance(self) -> float:
        """Free resonance of the passive radiator, Hz."""
        return 1.0 / (2.0 * math.pi * math.sqrt(self.mass * self.compliance))

    def impedance(self, omega: np.ndarray, medium: air.AirProperties) -> np.ndarray:
        mas = self.mass / self.area**2
        cas = self.compliance * self.area**2
        ras = self.resistance / self.area**2
        return ras + 1j * omega * mas + 1.0 / (1j * omega * cas)


@dataclass
class Solution:
    """The solved network: node pressures and derived quantities over frequency."""

    frequency: np.ndarray
    #: Node name -> complex pressure at each frequency, Pa.
    pressures: dict[str, np.ndarray]
    network: "Network"
    medium: air.AirProperties
    #: Frequency above which the lumped assumption fails, if known (§2.4).
    valid_below: float | None = None

    @property
    def omega(self) -> np.ndarray:
        return 2.0 * math.pi * self.frequency

    def _metadata(self) -> dict[str, str]:
        return {
            "solver": "lumped network",
            "medium": (
                f"{air.to_celsius(self.medium.temperature):.1f} C, "
                f"{self.medium.pressure:.0f} Pa, "
                f"{self.medium.humidity * 100:.0f}% RH"
            ),
            "speed of sound": f"{self.medium.speed_of_sound:.1f} m/s",
        }

    def pressure(self, node: str) -> ResponseCurve:
        """Sound pressure at a node.

        For a headphone this *is* the answer: the pressure in the ear cavity, no
        propagation required.
        """
        if node not in self.pressures:
            raise KeyError(f"unknown node {node!r}; known: {sorted(self.pressures)}")
        return ResponseCurve(
            self.frequency, self.pressures[node], quantity="pressure", unit="Pa",
            label=node, valid_below=self.valid_below, metadata=self._metadata(),
        )

    def node_pressure_array(self, node: str) -> np.ndarray:
        return self.pressures[node] if node != GROUND else np.zeros_like(self.frequency, dtype=complex)

    def volume_velocity(self, element_name: str) -> ResponseCurve:
        """Volume velocity through an element, m^3/s, positive from node_b to node_a."""
        element = self.network.element(element_name)
        z = element.impedance(self.omega, self.medium)
        delta = self.node_pressure_array(element.node_a) - self.node_pressure_array(element.node_b)
        flow = -delta / z
        source = element.source(self.omega, self.medium)
        if source is not None:
            flow = flow + source
        return ResponseCurve(
            self.frequency, flow, quantity="volume_velocity", unit="m^3/s",
            label=f"{element_name} flow", valid_below=self.valid_below, metadata=self._metadata(),
        )

    def excursion(self, driver_name: str) -> ResponseCurve:
        """Diaphragm displacement, metres peak. Compare against Xmax."""
        driver = self.network.element(driver_name)
        if not isinstance(driver, Driver):
            raise TypeError(f"{driver_name} is not a Driver")
        flow = self.volume_velocity(driver_name).values
        displacement = flow / (1j * self.omega * driver.parameters.Sd)
        return ResponseCurve(
            self.frequency, displacement, quantity="displacement", unit="m",
            label=f"{driver_name} excursion", valid_below=self.valid_below,
            metadata=self._metadata(),
        )

    def far_field_pressure(
        self,
        sources: Sequence[str] | Sequence[tuple[str, int]],
        distance: float = 1.0,
        *,
        half_space: bool = True,
    ) -> ResponseCurve:
        """Radiated pressure at ``distance`` metres from one or more radiating elements.

        A loudspeaker's answer, unlike a headphone's: nothing encloses the listener, so
        the result comes from volume *acceleration* rather than from a node pressure.
        For a source small against the wavelength,

            ``p = j omega rho U / (2 pi r)``   radiating into half space (in a baffle)
            ``p = j omega rho U / (4 pi r)``   radiating into full space

        ``sources`` names the elements that radiate. Because each element's volume
        velocity is signed relative to its own node ordering, an entry may be given as
        ``(name, sign)`` to flip it -- a vented box needs the port's flow oriented
        outward like the cone's before the two are summed. A bare name means ``+1``.
        """
        if not sources:
            raise ValueError("no radiating elements given")
        if distance <= 0.0:
            raise ValueError(f"distance must be positive, got {distance} m")

        total = np.zeros_like(self.frequency, dtype=complex)
        names: list[str] = []
        for source in sources:
            name, sign = source if isinstance(source, tuple) else (source, 1)
            total = total + sign * self.volume_velocity(name).values
            names.append(name)

        solid_angle = 2.0 if half_space else 4.0
        pressure = 1j * self.omega * self.medium.density * total / (solid_angle * math.pi * distance)
        return ResponseCurve(
            self.frequency, pressure, quantity="pressure", unit="Pa",
            label=f"far field {distance:g} m ({', '.join(names)})",
            valid_below=self.valid_below,
            metadata={**self._metadata(), "distance": f"{distance:g} m",
                      "space": "half" if half_space else "full"},
        )

    def _branch_impedance(self, driver_name: str) -> np.ndarray:
        """Load impedance of one driver branch as the amplifier sees it, ohms."""
        driver = self.network.element(driver_name)
        if not isinstance(driver, Driver):
            raise TypeError(f"{driver_name} is not a Driver")
        p = driver.parameters
        omega = self.omega
        terminal, looking_back = driver.drive(omega)
        coil = p.Re + 1j * omega * p.Le
        velocity = self.volume_velocity(driver_name).values / p.Sd
        # Polarity flips both BL and the resulting velocity, so their product -- and hence
        # the impedance -- is unchanged. Reversing a driver's wiring must not alter the
        # load the amplifier sees.
        back_emf = driver.polarity * p.BL * velocity
        current = (terminal - back_emf) / (coil + looking_back)
        # At the coil terminals the driver looks like its own blocked impedance plus the
        # motional impedance the back-EMF represents.
        coil_impedance = coil + back_emf / current
        if driver.filter is None:
            return coil_impedance
        return driver.filter.input_impedance(coil_impedance, omega)

    def input_impedance(self, driver_name: str) -> ResponseCurve:
        """Electrical impedance the amplifier sees looking into this branch, ohms.

        Rises to a peak at system resonance because the moving cone generates back-EMF.
        Measuring this curve is how the resonance of a real assembled system is found.

        With a crossover present this is measured at the *filter's* input, not the voice
        coil's -- that is what a meter across the terminals of a finished product reads,
        and it is what an amplifier has to drive. The amplifier's own output impedance is
        excluded, since it is not part of the load.
        """
        return ResponseCurve(
            self.frequency, self._branch_impedance(driver_name), quantity="impedance",
            unit="ohm", label=f"{driver_name} impedance", valid_below=self.valid_below,
            metadata=self._metadata(),
        )

    def system_impedance(self, driver_names: Sequence[str] | None = None) -> ResponseCurve:
        """Impedance of several branches wired in parallel across one amplifier, ohms.

        The curve a two-way presents at its plug. Worth looking at: a passive crossover can
        dip well below the nominal impedance of either driver where the two branches
        conduct at once, and that dip is what an amplifier actually has to survive.

        Assumes the branches share one amplifier of negligible output impedance, which is
        the same assumption that lets each branch carry an independent filter.
        """
        names = list(driver_names) if driver_names else [d.name for d in self.network.drivers]
        if not names:
            raise ValueError("no drivers to combine")
        admittance = np.zeros_like(self.frequency, dtype=complex)
        for name in names:
            admittance = admittance + 1.0 / self._branch_impedance(name)
        return ResponseCurve(
            self.frequency, 1.0 / admittance, quantity="impedance", unit="ohm",
            label=f"system impedance ({', '.join(names)})", valid_below=self.valid_below,
            metadata=self._metadata(),
        )


class Network:
    """A topology of acoustic elements, solved by nodal analysis."""

    def __init__(self, medium: air.AirProperties | None = None) -> None:
        self.elements: list[Element] = []
        self.medium = medium or air.AirProperties.at()

    def add(self, element: Element) -> Element:
        """Add an element. Names must be unique so results can refer to them."""
        if any(e.name == element.name for e in self.elements):
            raise ValueError(f"duplicate element name {element.name!r}")
        self.elements.append(element)
        return element

    def extend(self, elements: Iterable[Element]) -> None:
        for element in elements:
            self.add(element)

    def element(self, name: str) -> Element:
        for element in self.elements:
            if element.name == name:
                return element
        raise KeyError(f"no element named {name!r}; known: {sorted(e.name for e in self.elements)}")

    @property
    def drivers(self) -> list[Driver]:
        return [e for e in self.elements if isinstance(e, Driver)]

    def node_names(self) -> list[str]:
        """Every non-ground node, in a stable order."""
        seen: list[str] = []
        for element in self.elements:
            for node in element.nodes:
                if node != GROUND and node not in seen:
                    seen.append(node)
        return seen

    def floating_nodes(self) -> list[str]:
        """Nodes touched by only one element.

        Almost always a wiring mistake: a port that goes nowhere, or a driver whose back
        was never connected. Such a node has no path to ground and makes the matrix
        singular, so this is checked before solving to give a comprehensible error.
        """
        counts: dict[str, int] = {}
        for element in self.elements:
            for node in element.nodes:
                if node != GROUND:
                    counts[node] = counts.get(node, 0) + 1
        return sorted(node for node, count in counts.items() if count < 2)

    def solve(
        self,
        frequency: Sequence[float] | np.ndarray,
        *,
        valid_below: float | None = None,
    ) -> Solution:
        """Solve at every frequency and return the node pressures.

        Assembles ``Y p = I`` where ``Y`` is the nodal admittance matrix in acoustic
        units, ``p`` the node pressures and ``I`` the injected volume velocities. The
        whole sweep is one batched solve.
        """
        if not self.elements:
            raise ValueError("cannot solve an empty network")
        floating = self.floating_nodes()
        if floating:
            raise ValueError(
                f"node(s) {', '.join(floating)} connect to only one element, so they have "
                f"no path to ground. Every node needs at least two connections -- check "
                f"for a driver whose back node or a port whose far end was left unattached."
            )

        frequency = np.asarray(frequency, dtype=float)
        if frequency.ndim != 1 or frequency.size == 0:
            raise ValueError("frequency must be a non-empty one-dimensional array")
        if np.any(frequency <= 0.0):
            raise ValueError("frequencies must be positive")

        omega = 2.0 * math.pi * frequency
        nodes = self.node_names()
        index = {node: i for i, node in enumerate(nodes)}
        n, nf = len(nodes), frequency.size

        Y = np.zeros((nf, n, n), dtype=complex)
        I = np.zeros((nf, n), dtype=complex)

        for element in self.elements:
            y = 1.0 / element.impedance(omega, self.medium)
            a, b = element.node_a, element.node_b
            if a != GROUND:
                ia = index[a]
                Y[:, ia, ia] += y
            if b != GROUND:
                ib = index[b]
                Y[:, ib, ib] += y
            if a != GROUND and b != GROUND:
                ia, ib = index[a], index[b]
                Y[:, ia, ib] -= y
                Y[:, ib, ia] -= y

            source = element.source(omega, self.medium)
            if source is not None:
                if a != GROUND:
                    I[:, index[a]] += source
                if b != GROUND:
                    I[:, index[b]] -= source

        try:
            # NumPy 2 treats a trailing dimension as a matrix unless b is strictly 1-D, so
            # the right-hand side is given an explicit column axis and squeezed after.
            solved = np.linalg.solve(Y, I[:, :, np.newaxis])[:, :, 0]
        except np.linalg.LinAlgError as exc:
            raise ValueError(
                f"the network could not be solved ({exc}). This usually means a node is "
                f"isolated or two elements form a loop with no resistance."
            ) from exc

        return Solution(
            frequency=frequency,
            pressures={node: solved[:, i] for node, i in index.items()},
            network=self,
            medium=self.medium,
            valid_below=valid_below,
        )
