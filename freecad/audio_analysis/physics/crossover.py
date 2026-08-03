"""Crossover filters: the electrical network between the amplifier and a driver.

A two-way headphone is not two independent single-driver models added together. The
drivers share air (STRUCTURE.md §2.4) *and* they share a frequency axis, and what each
one contributes is set by the filter in front of it. Without crossovers the Tier 1 model
can only drive every driver with the same voltage at every frequency, which is not a
system anyone would build.

**Where a filter enters the solve.** A filter changes two things at the voice coil: the
voltage that arrives, and the impedance looking back toward the amplifier. Both are
frequency dependent, and both are already parameters of
:class:`~freecad.audio_analysis.physics.network.Driver` -- so a filter is folded in by
generalising ``voltage`` and ``source_impedance`` from numbers to curves. Nothing about
the nodal acoustic solve changes.

The back-looking impedance is not a detail. A driver's damping comes partly from its own
motor working into whatever resistance it sees (the ``BL^2/(Sd^2 Ze)`` term), so a series
inductor ahead of a woofer raises its Q as well as rolling off its top end. Modelling the
filter as a bare voltage multiplier misses that entirely.

**Two realisations.**

*Active* -- the filter runs at line level and each driver has its own power amplifier. The
transfer function is whatever we say it is, the output impedance is the amplifier's, and
delay is available. This is exact for a DSP crossover.

*Passive* -- an inductor/capacitor ladder between one amplifier and the driver. Component
values are synthesised from the requested alignment, but the response you get is **not**
the requested alignment: textbook values assume a resistive load, and a driver is not a
resistor. Its impedance rises at resonance and again with voice-coil inductance, so a real
passive crossover always deviates from its nominal target. This module computes the loaded
response rather than the nominal one, which is the entire reason to simulate it.

**Sharing one amplifier.** Several passive branches hung off one amplifier do not interact
as long as the amplifier's output impedance is zero -- it holds the common node at a fixed
voltage no matter what the other branch draws. That is the normal case (a damping factor
above about 50 makes it true to a fraction of a dB), and it is why each driver may carry
its own independent filter here. Set a non-zero source impedance and the branches really
do load each other; that coupling is not modelled, and the checks say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy import signal

#: Filter shapes. "Bypass" still applies gain and delay, which is how a tweeter gets
#: padded down without being filtered.
RESPONSES = ("Lowpass", "Highpass", "Bypass")

#: Alignments the ladder synthesis and the ideal transfer both understand.
ALIGNMENTS = ("Butterworth", "Linkwitz-Riley", "Bessel")

#: Highest order supported. Beyond this the phase behaviour makes summation guesswork and
#: a passive realisation becomes impractical.
MAX_ORDER = 8


class CrossoverError(ValueError):
    """Raised when a filter cannot be built from the requested description."""


# ---------------------------------------------------------------------------------
# Prototype polynomials
# ---------------------------------------------------------------------------------


def prototype_denominator(alignment: str, order: int) -> np.ndarray:
    """Normalised low-pass denominator ``D(s)``, highest power first, with ``D(0) == 1``.

    The prototype is cut off at ``s = j``; denormalising to a real corner frequency is a
    substitution of ``s -> s/omega_c``, done by the callers.

    *Butterworth* is maximally flat in amplitude and the default for a single driver.
    *Linkwitz-Riley* is two cascaded Butterworth sections of half the order, so each branch
    is 6 dB down at the crossover rather than 3 dB; the two branches then sum flat, which
    is why it is the usual choice for a multi-way system. It exists only at even orders.
    *Bessel* trades amplitude flatness for constant group delay.
    """
    if alignment not in ALIGNMENTS:
        raise CrossoverError(f"unknown alignment {alignment!r}; known: {list(ALIGNMENTS)}")
    if not 1 <= order <= MAX_ORDER:
        raise CrossoverError(f"order must be between 1 and {MAX_ORDER}, got {order}")

    if alignment == "Butterworth":
        _, a = signal.butter(order, 1.0, btype="low", analog=True, output="ba")
    elif alignment == "Bessel":
        # norm="mag" puts the -3 dB point at 1 rad/s, matching the other alignments so
        # that changing alignment does not silently move the crossover frequency.
        _, a = signal.bessel(order, 1.0, btype="low", analog=True, norm="mag")
    else:  # Linkwitz-Riley: a Butterworth of half the order, cascaded with itself.
        if order % 2:
            raise CrossoverError(
                f"Linkwitz-Riley exists only at even orders, got {order}. It is defined as "
                f"two cascaded Butterworth sections; use Butterworth for an odd order."
            )
        _, half = signal.butter(order // 2, 1.0, btype="low", analog=True, output="ba")
        a = np.convolve(half, half)

    return np.asarray(a, dtype=float) / a[-1]


def ideal_transfer(alignment: str, order: int, response: str, s: np.ndarray) -> np.ndarray:
    """Voltage transfer of the ideal filter at normalised complex frequency ``s``.

    The high-pass is the low-pass reflected about the corner, ``H(1/s)``, which is the same
    substitution the passive element transformation performs -- so the two realisations
    describe one filter rather than two similar ones. Algebraically that is
    ``s^n / D_reversed(s)``, with the coefficients of ``D`` read back to front.

    ``D_reversed`` matters only for asymmetric alignments. Butterworth and Linkwitz-Riley
    polynomials are palindromic, so their high-pass is the more familiar ``s^n / D(s)``;
    Bessel's is not, and using ``D`` unreversed there gives a filter that is neither a
    Bessel high-pass nor the ladder that gets built for it.
    """
    if response == "Bypass":
        return np.ones_like(s, dtype=complex)
    if response not in RESPONSES:
        raise CrossoverError(f"unknown response {response!r}; known: {list(RESPONSES)}")

    denominator = prototype_denominator(alignment, order)
    if response == "Lowpass":
        return 1.0 / np.polyval(denominator, s)
    return s**order / np.polyval(denominator[::-1], s)


# ---------------------------------------------------------------------------------
# Ladder synthesis
# ---------------------------------------------------------------------------------


def ladder_prototype(alignment: str, order: int) -> list[float]:
    """Normalised element values ``g1..gn`` for a singly-terminated low-pass ladder.

    The ladder runs source -> ``g1`` (series inductor) -> ``g2`` (shunt capacitor) -> ...
    -> 1 ohm load, at a 1 rad/s corner. Singly terminated means the source impedance is
    zero, which is what an amplifier looks like; the doubly-terminated values found in
    filter textbooks are for a different problem and would give the wrong response here.

    Synthesised rather than tabulated. Splitting ``D(s)`` into its even part ``m`` and odd
    part ``n``, the reactance function of the ladder seen from the *load* end is the ratio
    of whichever has the higher degree to the other; expanding it as a continued fraction
    about ``s = infinity`` peels off one element per step, in load-to-source order. Doing
    it this way means a new alignment needs no new table, and the values are checked
    against the alignment they came from in the tests rather than against my typing.
    """
    denominator = prototype_denominator(alignment, order)
    # np.polyval order is highest power first; index from the end to select by power.
    powers = denominator[::-1]
    even = np.zeros(order + 1)
    odd = np.zeros(order + 1)
    even[0::2] = powers[0::2]
    odd[1::2] = powers[1::2]

    numerator, divisor = (odd[::-1], even[::-1]) if order % 2 else (even[::-1], odd[::-1])
    numerator = np.trim_zeros(numerator, "f")
    divisor = np.trim_zeros(divisor, "f")

    values: list[float] = []
    for _ in range(order):
        quotient, remainder = np.polydiv(numerator, divisor)
        if quotient.size != 2 or abs(quotient[1]) > 1e-9 * max(1.0, abs(quotient[0])):
            raise CrossoverError(
                f"the {alignment} order-{order} prototype does not reduce to a simple "
                f"LC ladder, so it has no passive realisation of this form."
            )
        values.append(float(quotient[0]))
        numerator, divisor = divisor, np.trim_zeros(remainder, "f")
        if divisor.size == 0:
            break

    values.reverse()  # Synthesis runs load-to-source; users read source-to-load.
    return values


@dataclass(frozen=True)
class Component:
    """One element of a realised ladder."""

    #: "L" (inductor) or "C" (capacitor) or "R" (resistor).
    kind: str
    #: "series" (in line with the signal) or "shunt" (across it).
    placement: str
    #: Henries, farads or ohms, matching ``kind``.
    value: float

    def impedance(self, omega: np.ndarray) -> np.ndarray:
        if self.kind == "L":
            return 1j * omega * self.value
        if self.kind == "C":
            return 1.0 / (1j * omega * self.value)
        return np.full(omega.shape, self.value, dtype=complex)

    def describe(self) -> str:
        if self.kind == "L":
            magnitude = f"{self.value * 1e3:.3g} mH"
        elif self.kind == "C":
            magnitude = f"{self.value * 1e6:.3g} uF"
        else:
            magnitude = f"{self.value:.3g} ohm"
        return f"{self.placement} {self.kind} {magnitude}"


def synthesise(
    alignment: str, order: int, response: str, frequency: float, impedance: float
) -> list[Component]:
    """Component values for a passive filter cut off at ``frequency`` into ``impedance``.

    Denormalising the low-pass prototype is a scaling: a series ``g`` becomes an inductor
    ``g R / omega_c``, a shunt ``g`` becomes a capacitor ``g / (R omega_c)``.

    The high-pass is the standard LC transformation of the same prototype -- every series
    inductor becomes a series capacitor and every shunt capacitor a shunt inductor, with
    the value reciprocated. It is not a separate synthesis.
    """
    if response == "Bypass":
        return []
    if frequency <= 0.0:
        raise CrossoverError(f"crossover frequency must be positive, got {frequency} Hz")
    if impedance <= 0.0:
        raise CrossoverError(f"nominal impedance must be positive, got {impedance} ohm")

    omega_c = 2.0 * math.pi * frequency
    components: list[Component] = []
    for index, g in enumerate(ladder_prototype(alignment, order)):
        series = index % 2 == 0
        if response == "Lowpass":
            kind = "L" if series else "C"
            value = g * impedance / omega_c if series else g / (impedance * omega_c)
        else:
            kind = "C" if series else "L"
            value = 1.0 / (g * impedance * omega_c) if series else impedance / (g * omega_c)
        components.append(Component(kind, "series" if series else "shunt", value))
    return components


def l_pad(attenuation_db: float, impedance: float) -> list[Component]:
    """A resistive attenuator that presents a constant ``impedance`` to the filter.

    The passive answer to a tweeter being more sensitive than a woofer, which it almost
    always is. A bare series resistor would attenuate, but it would also change the load
    the filter sees and so shift the crossover; the two-resistor L-pad holds the input
    impedance at ``R`` for any attenuation, which is why it is the standard form.

    For a linear ratio ``a``, ``R_series = R(1 - a)`` and ``R_shunt = R a / (1 - a)``.
    """
    if attenuation_db >= 0.0:
        return []
    ratio = 10.0 ** (attenuation_db / 20.0)
    return [
        Component("R", "series", impedance * (1.0 - ratio)),
        Component("R", "shunt", impedance * ratio / (1.0 - ratio)),
    ]


# ---------------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------------


class Filter:
    """What a crossover branch does to the drive reaching one voice coil.

    **Why not a Thevenin equivalent.** The obvious interface is an open-circuit voltage
    and an output impedance, and it is wrong. A lossless LC ladder has *infinite*
    open-circuit gain at its own resonance -- with nothing loading it, the tank rings
    without limit -- and for a second-order crossover that resonance sits exactly at the
    crossover frequency, which is the one frequency a user is guaranteed to put in their
    sweep. Both the open-circuit voltage and the output impedance diverge there while
    every physical quantity stays finite, so the singularity is removable but only if it
    is never formed.

    So a filter instead reports three coefficients ``(gain, alpha, beta)`` giving the coil
    current directly:

        ``i = (V_amp * gain - alpha * emf) / (alpha * Zc + beta)``

    with ``Zc`` the blocked coil impedance and ``emf`` the back-EMF the moving cone
    generates. Dividing through by ``alpha`` recovers the Thevenin form -- open-circuit
    voltage ``V*gain/alpha``, output impedance ``beta/alpha`` -- which is exactly the
    division that must not happen.
    """

    def terminal_coefficients(
        self, omega: np.ndarray, source_impedance: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(gain, alpha, beta)`` for the coil-current relation above.

        Independent of the driver, so computable before the acoustic solve -- which is
        what keeps a crossover from making the problem circular.
        """
        raise NotImplementedError

    def amplifier_impedance(
        self, terminal_voltage: np.ndarray, coil_current: np.ndarray, omega: np.ndarray
    ) -> np.ndarray:
        """Impedance at the amplifier terminals, from the solved coil quantities."""
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass
class IdealFilter(Filter):
    """A line-level filter followed by its own power amplifier.

    Exact for a DSP or active analogue crossover. The transfer function is realised
    perfectly, and the driver is damped by the power amplifier rather than by the filter,
    so ``source_impedance`` passes straight through.
    """

    response: str = "Bypass"
    alignment: str = "Butterworth"
    order: int = 2
    frequency: float = 2000.0
    gain_db: float = 0.0
    delay: float = 0.0

    def __post_init__(self) -> None:
        """Reject an impossible description at construction, not at solve time.

        Otherwise an odd-order Linkwitz-Riley would sit in the document looking valid --
        the summary line would describe it and the checks would pass -- until a solve
        finally evaluated it. A bypassed branch skips this: its alignment and order are
        not used, and stale values there are not a mistake.
        """
        if self.response == "Bypass":
            return
        if self.frequency <= 0.0:
            raise CrossoverError(f"crossover frequency must be positive, got {self.frequency} Hz")
        prototype_denominator(self.alignment, self.order)

    def transfer(self, omega: np.ndarray) -> np.ndarray:
        s = 1j * omega / (2.0 * math.pi * self.frequency)
        shape = ideal_transfer(self.alignment, self.order, self.response, s)
        return shape * 10.0 ** (self.gain_db / 20.0) * np.exp(-1j * omega * self.delay)

    def terminal_coefficients(
        self, omega: np.ndarray, source_impedance: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        # The filter is realised perfectly and the driver is damped by its own power
        # amplifier, so the transfer function is a plain gain and nothing can diverge.
        ones = np.ones(omega.shape, dtype=complex)
        return self.transfer(omega), ones, source_impedance * ones

    def amplifier_impedance(
        self, terminal_voltage: np.ndarray, coil_current: np.ndarray, omega: np.ndarray
    ) -> np.ndarray:
        # Each driver has its own amplifier, so the load is the coil itself.
        return terminal_voltage / coil_current

    def describe(self) -> str:
        if self.response == "Bypass":
            shape = "bypass"
        else:
            shape = (
                f"{self.alignment} order {self.order} {self.response.lower()} "
                f"at {self.frequency:g} Hz"
            )
        extras = []
        if self.gain_db:
            extras.append(f"{self.gain_db:+.1f} dB")
        if self.delay:
            extras.append(f"{self.delay * 1e6:.0f} us delay")
        return "active: " + shape + ("; " + ", ".join(extras) if extras else "")


@dataclass
class PassiveLadder(Filter):
    """A real LC ladder between one amplifier and the driver.

    Held as a cascade of two-port ABCD matrices, which is what makes the loaded response
    fall out without assuming anything about the load. For the chain matrix
    ``[[A, B], [C, D]]`` and an amplifier of output impedance ``Zs``, the coefficients of
    :meth:`Filter.terminal_coefficients` are

        ``gain = 1``,   ``alpha = A + Zs C``,   ``beta = B + Zs D``

    and the amplifier sees ``(A V2 + B I2) / (C V2 + D I2)``. Written this way nothing
    diverges at the ladder's own resonance, where ``alpha`` passes through zero.

    An empty component list is the identity matrix, so a bypassed passive branch reduces
    to a direct connection rather than being a special case.
    """

    components: list[Component] = field(default_factory=list)
    label: str = "passive"

    def abcd(self, omega: np.ndarray) -> tuple[np.ndarray, ...]:
        ones = np.ones(omega.shape, dtype=complex)
        zeros = np.zeros(omega.shape, dtype=complex)
        A, B, C, D = ones, zeros, zeros, ones
        for component in self.components:
            z = component.impedance(omega)
            if component.placement == "series":
                a, b, c, d = ones, z, zeros, ones
            else:
                a, b, c, d = ones, zeros, 1.0 / z, ones
            A, B, C, D = A * a + B * c, A * b + B * d, C * a + D * c, C * b + D * d
        return A, B, C, D

    def terminal_coefficients(
        self, omega: np.ndarray, source_impedance: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        A, B, C, D = self.abcd(omega)
        return (
            np.ones(omega.shape, dtype=complex),
            A + source_impedance * C,
            B + source_impedance * D,
        )

    def amplifier_impedance(
        self, terminal_voltage: np.ndarray, coil_current: np.ndarray, omega: np.ndarray
    ) -> np.ndarray:
        A, B, C, D = self.abcd(omega)
        return (A * terminal_voltage + B * coil_current) / (
            C * terminal_voltage + D * coil_current
        )

    def describe(self) -> str:
        if not self.components:
            return f"{self.label}: direct connection"
        return f"{self.label}: " + ", ".join(c.describe() for c in self.components)


def make_filter(
    *,
    response: str = "Bypass",
    alignment: str = "Butterworth",
    order: int = 2,
    frequency: float = 2000.0,
    gain_db: float = 0.0,
    delay: float = 0.0,
    passive: bool = False,
    impedance: float = 32.0,
) -> Filter:
    """Build the filter a crossover object describes.

    Passive realisation drops ``delay``, which no arrangement of inductors and capacitors
    provides, and turns ``gain_db`` into an L-pad -- so only attenuation is available.
    Both limits are reported by the Tier 1 checks rather than being silently applied.
    """
    if not passive:
        return IdealFilter(response, alignment, order, frequency, gain_db, delay)

    components = synthesise(alignment, order, response, frequency, impedance)
    components += l_pad(gain_db, impedance)
    return PassiveLadder(components)


def summing_response(filters: Sequence[Filter], omega: np.ndarray, load: float = 8.0):
    """Voltage transfers of several branches into a resistive ``load``, summed complexly.

    A quick way to see whether a pair of filters sums flat before any driver is involved.
    The real system response is the acoustic sum from the solve; this is the electrical
    half of it, isolated -- and into a resistor, which is the condition the alignment was
    designed for and not the condition a driver provides.
    """
    total = np.zeros(omega.shape, dtype=complex)
    for filter_ in filters:
        gain, alpha, beta = filter_.terminal_coefficients(omega, 0.0)
        # Into a pure resistance the coil "emf" is zero, so v_load = load * i.
        total = total + gain * load / (alpha * load + beta)
    return total
