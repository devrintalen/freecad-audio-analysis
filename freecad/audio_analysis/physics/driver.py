"""Electrodynamic driver parameters.

A moving-coil driver's small-signal behaviour is fully described by a handful of numbers,
the **Thiele–Small parameters**. They are what a datasheet publishes and what a measurement
rig produces, so they are the natural input.

Only a subset is independent; the rest follow. A datasheet typically gives ``fs``, ``Re``,
``Qms``, ``Qes``, ``Sd`` and either ``Vas`` or ``Mms``, and everything else is derived.
:meth:`DriverParameters.from_thiele_small` does that derivation once, so no downstream code
has to guess which form it was handed.

All values are SI: metres, kilograms, seconds, ohms, tesla-metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from freecad.audio_analysis.physics import air


@dataclass(frozen=True)
class DriverParameters:
    """A complete, self-consistent small-signal driver description."""

    name: str = "driver"

    #: DC resistance of the voice coil, ohms.
    Re: float = 6.0
    #: Voice coil inductance, henries. Small but it rolls off the top end.
    Le: float = 0.0
    #: Force factor B*l, tesla-metres (equivalently N/A).
    BL: float = 5.0
    #: Moving mass including the air load, kg.
    Mms: float = 5.0e-3
    #: Suspension compliance, m/N.
    Cms: float = 1.0e-3
    #: Mechanical resistance of the suspension, N*s/m.
    Rms: float = 0.5
    #: Effective radiating area, m^2.
    Sd: float = 5.0e-3
    #: Maximum linear excursion, m (one-way peak).
    Xmax: float = 1.0e-3

    def __post_init__(self) -> None:
        for field_name in ("Re", "BL", "Mms", "Cms", "Sd"):
            if getattr(self, field_name) <= 0.0:
                raise ValueError(f"{field_name} must be positive, got {getattr(self, field_name)}")
        if self.Rms < 0.0 or self.Le < 0.0:
            raise ValueError("Rms and Le must not be negative")

    # -- derived quantities ----------------------------------------------------------

    @property
    def fs(self) -> float:
        """Free-air resonance, Hz."""
        return 1.0 / (2.0 * math.pi * math.sqrt(self.Mms * self.Cms))

    @property
    def omega_s(self) -> float:
        return 2.0 * math.pi * self.fs

    @property
    def Qms(self) -> float:
        """Mechanical Q: how lightly the suspension is damped."""
        if self.Rms == 0.0:
            return math.inf
        return self.omega_s * self.Mms / self.Rms

    @property
    def Qes(self) -> float:
        """Electrical Q: damping the motor imposes through the amplifier."""
        return self.omega_s * self.Mms * self.Re / (self.BL**2)

    @property
    def Qts(self) -> float:
        """Total Q at resonance -- the single most telling number about a driver.

        Below ~0.4 the driver wants a large or vented enclosure; near 0.7 it is close to
        maximally flat sealed; above ~1 it will sound peaky in most boxes.
        """
        qms, qes = self.Qms, self.Qes
        if math.isinf(qms):
            return qes
        return qms * qes / (qms + qes)

    def Vas(self, medium: air.AirProperties | None = None) -> float:
        """Equivalent suspension volume, m^3.

        The volume of air whose acoustic compliance equals the suspension's. Comparing it
        with the enclosure volume is what predicts the resonance shift a box causes.
        """
        medium = medium or air.AirProperties.at()
        return medium.density * medium.speed_of_sound**2 * self.Cms * self.Sd**2

    @property
    def Cas(self) -> float:
        """Suspension compliance referred to the acoustical domain, m^3/Pa."""
        return self.Cms * self.Sd**2

    @property
    def Mas(self) -> float:
        """Moving mass referred to the acoustical domain, kg/m^4."""
        return self.Mms / self.Sd**2

    @property
    def Ras(self) -> float:
        """Mechanical loss referred to the acoustical domain, Pa*s/m^3."""
        return self.Rms / self.Sd**2

    @property
    def radius(self) -> float:
        """Radius of a circular piston with the same area, m."""
        return math.sqrt(self.Sd / math.pi)

    @classmethod
    def from_thiele_small(
        cls,
        *,
        name: str = "driver",
        fs: float,
        Re: float,
        Qms: float,
        Qes: float,
        Sd: float,
        Vas: float | None = None,
        Mms: float | None = None,
        Le: float = 0.0,
        Xmax: float = 1.0e-3,
        medium: air.AirProperties | None = None,
    ) -> "DriverParameters":
        """Build from the parameters a datasheet actually publishes.

        Supply exactly one of ``Vas`` (m^3) or ``Mms`` (kg); the other is derived. Vas is
        the more commonly published, but it depends on air properties, so the medium is
        taken into account rather than assumed.
        """
        if (Vas is None) == (Mms is None):
            raise ValueError("supply exactly one of Vas or Mms")
        for value, label in ((fs, "fs"), (Re, "Re"), (Qms, "Qms"), (Qes, "Qes"), (Sd, "Sd")):
            if value <= 0.0:
                raise ValueError(f"{label} must be positive, got {value}")

        medium = medium or air.AirProperties.at()
        omega_s = 2.0 * math.pi * fs

        if Vas is not None:
            if Vas <= 0.0:
                raise ValueError(f"Vas must be positive, got {Vas}")
            Cms = Vas / (medium.density * medium.speed_of_sound**2 * Sd**2)
            Mms = 1.0 / (omega_s**2 * Cms)
        else:
            if Mms <= 0.0:
                raise ValueError(f"Mms must be positive, got {Mms}")
            Cms = 1.0 / (omega_s**2 * Mms)

        Rms = omega_s * Mms / Qms
        BL = math.sqrt(omega_s * Mms * Re / Qes)

        return cls(
            name=name, Re=Re, Le=Le, BL=BL, Mms=Mms, Cms=Cms, Rms=Rms, Sd=Sd, Xmax=Xmax
        )

    def sealed_box_resonance(self, box_volume: float, medium: air.AirProperties | None = None) -> float:
        """Closed-form system resonance in a sealed box of ``box_volume`` m^3.

        ``fc = fs * sqrt(1 + Vas/Vb)``. The box adds stiffness, so the resonance always
        rises. Provided as an independent check on the network solver, not as its
        replacement -- see tests/test_network.py.
        """
        if box_volume <= 0.0:
            raise ValueError(f"box volume must be positive, got {box_volume}")
        return self.fs * math.sqrt(1.0 + self.Vas(medium) / box_volume)

    def sealed_box_q(self, box_volume: float, medium: air.AirProperties | None = None) -> float:
        """Closed-form total Q in a sealed box: ``Qtc = Qts * sqrt(1 + Vas/Vb)``."""
        if box_volume <= 0.0:
            raise ValueError(f"box volume must be positive, got {box_volume}")
        return self.Qts * math.sqrt(1.0 + self.Vas(medium) / box_volume)

    def describe(self) -> str:
        """One-line summary in the units a datasheet uses."""
        return (
            f"{self.name}: fs={self.fs:.1f} Hz, Qts={self.Qts:.3f}, "
            f"Re={self.Re:.1f} ohm, Sd={self.Sd * 1e4:.1f} cm^2, "
            f"Vas={self.Vas() * 1000:.2f} l, Xmax={self.Xmax * 1000:.2f} mm"
        )
