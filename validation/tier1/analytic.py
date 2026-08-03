"""Tier 1 benchmarks with closed-form answers.

Each case here compares the network solver against a result that can be written down
without it. Where the closed form is standard loudspeaker theory the algebra is restated
in the docstring, so a failure can be diagnosed without hunting for the textbook.
"""

from __future__ import annotations

import math

import numpy as np

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.physics.network import (
    GROUND,
    AcousticMass,
    Compliance,
    Driver,
    Network,
    PistonRadiation,
)
from validation.harness import Comparison, case

MEDIUM = air.AirProperties.at()

#: A conventional 6.5 inch woofer, used wherever a case needs a driver.
WOOFER = DriverParameters.from_thiele_small(
    name="woofer", fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=133e-4, Vas=10e-3, Le=0.0
)

FREQUENCY = np.logspace(0.5, 3.5, 8000)

#: A compliance this large presents essentially no acoustic impedance, which is how "no
#: acoustic load" is expressed in a network where every element must connect two nodes.
#: At 20 Hz it contributes about 0.1 Pa*s/m^3 against a driver impedance of order 1e5, so
#: it perturbs a result by roughly one part in a million. A merely *large* volume is not
#: enough: at 1 m^3 the residual load shifts the resonance benchmarks by almost 1%, which
#: is how this number was arrived at.
FREE_AIR_M3 = 1.0e4


def resonance_of(curve_frequency: np.ndarray, magnitude: np.ndarray) -> float:
    """Frequency of the largest peak, refined by a parabolic fit on a log axis."""
    index = int(np.argmax(magnitude))
    if 0 < index < magnitude.size - 1:
        x = np.log10(curve_frequency[index - 1: index + 2])
        y = magnitude[index - 1: index + 2]
        denominator = y[0] - 2.0 * y[1] + y[2]
        if denominator != 0.0:
            offset = 0.5 * (y[0] - y[2]) / denominator
            return float(10.0 ** (x[1] + offset * (x[1] - x[0])))
    return float(curve_frequency[index])


# ---------------------------------------------------------------------------------


@case(
    "sealed_box",
    "Sealed box: resonance and Q",
    "Closed-form Thiele-Small sealed alignment: fc = fs*sqrt(1 + Vas/Vb), Qtc = Qts*fc/fs",
    tier=1,
)
def sealed_box():
    """A sealed enclosure is a spring behind the cone.

    Adding it in parallel with the suspension raises the system resonance by the square
    root of the compliance ratio and scales Q in the same proportion. Both are exact for
    a lossless box, so the network solver has nowhere to hide.
    """
    volume = 20e-3  # 20 litres
    network = Network(MEDIUM)
    network.add(Driver("D", WOOFER, front_node="Front", back_node="Box", voltage=2.83))
    network.add(Compliance("box", volume, "Box"))
    # A very large front volume presents almost no acoustic load, isolating the effect of
    # the box alone -- radiation loading is checked separately.
    network.add(Compliance("front", FREE_AIR_M3, "Front"))

    solution = network.solve(FREQUENCY)
    impedance = solution.input_impedance("D")
    measured = resonance_of(impedance.frequency, impedance.magnitude)

    ratio = WOOFER.Vas(MEDIUM) / volume
    expected_fc = WOOFER.fs * math.sqrt(1.0 + ratio)
    expected_qtc = WOOFER.Qts * expected_fc / WOOFER.fs

    resonance, qms, qes, qts = _q_from_impedance(
        impedance.frequency, impedance.magnitude, WOOFER.Re
    )

    return [
        Comparison("system resonance fc", measured, expected_fc, 0.005, "Hz"),
        Comparison(
            "fc from the impedance-curve method", resonance, expected_fc, 0.005, "Hz",
            note="Geometric mean of the two half-power frequencies, which is how fc is "
                 "read off a measured impedance curve.",
        ),
        Comparison("total Q at resonance", qts, expected_qtc, 0.01),
        Comparison(
            "mechanical Q at resonance", qms, WOOFER.Qms * expected_fc / WOOFER.fs, 0.01,
        ),
        Comparison(
            "electrical Q at resonance", qes, WOOFER.Qes * expected_fc / WOOFER.fs, 0.01,
            note="All three Q values are extracted from the impedance curve by the "
                 "standard bench procedure, so the curve's whole shape is under test and "
                 "not merely the location of its peak.",
        ),
    ]


def _q_from_impedance(
    frequency: np.ndarray, magnitude: np.ndarray, re: float
) -> tuple[float, float, float, float]:
    """``(fc, Qms, Qes, Qts)`` by the standard impedance-curve method.

    The procedure used on a bench, and therefore a genuinely independent reference. With
    ``r0 = |Z|max / Re``, the two frequencies where ``|Z| = Re * sqrt(r0)`` bracket the
    resonance; their geometric mean is ``fc``, and

        Qms = fc * sqrt(r0) / (f2 - f1),   Qes = Qms / (r0 - 1),   Qts = QmsQes/(Qms+Qes)

    Reading Q off the width of an *excursion* peak instead does not work below Q = 0.707,
    where there is no peak at all -- only a plateau whose width says nothing.
    """
    peak = int(np.argmax(magnitude))
    r0 = magnitude[peak] / re
    target = re * math.sqrt(r0)

    below = np.where(magnitude[:peak] <= target)[0]
    above = np.where(magnitude[peak:] <= target)[0]
    if below.size == 0 or above.size == 0:
        raise ValueError("the impedance curve does not fall to its half-power points")
    f1 = float(np.interp(target, magnitude[below[-1]: peak + 1], frequency[below[-1]: peak + 1]))
    high = peak + above[0]
    f2 = float(
        np.interp(target, magnitude[high: peak - 1: -1], frequency[high: peak - 1: -1])
    )

    fc = math.sqrt(f1 * f2)
    qms = fc * math.sqrt(r0) / (f2 - f1)
    qes = qms / (r0 - 1.0)
    return fc, qms, qes, qms * qes / (qms + qes)


@case(
    "vented_box",
    "Vented box: Helmholtz tuning frequency",
    "Analytic Helmholtz resonance fb = (c/2pi) sqrt(S / (V L_eff))",
    tier=1,
)
def vented_box():
    """A port is a mass; the box is a spring; together they are a Helmholtz resonator.

    Measured the way it is measured on a bench: at the tuning frequency the box and port
    resonate, the load on the rear of the diaphragm peaks, the cone is held nearly still,
    and the motional part of the electrical impedance vanishes — so ``fb`` is the minimum
    between the two impedance peaks. That probe is *independent of the driver*, because
    the load it sees is the box compliance in parallel with the port mass and nothing
    else. Reading the box pressure instead would not be: the driver's own acoustic
    impedance sits in parallel with the resonator and pulls the apparent peak up by 20%.

    The end correction is part of what is under test. It is applied inside
    ``AcousticMass``, and omitting it would move this number by a third.
    """
    volume = 40e-3
    area = 40e-4
    length = 0.15

    port = AcousticMass("port", area=area, length=length, node_a="Box", node_b=GROUND)
    network = Network(MEDIUM)
    network.add(Compliance("box", volume, "Box"))
    network.add(port)
    network.add(Driver("D", WOOFER, front_node="Front", back_node="Box", voltage=2.83))
    network.add(Compliance("front", FREE_AIR_M3, "Front"))

    solution = network.solve(FREQUENCY)
    impedance = solution.input_impedance("D").magnitude
    window = (FREQUENCY > 15.0) & (FREQUENCY < 200.0)
    measured = float(FREQUENCY[window][np.argmin(impedance[window])])

    effective_length = port.effective_length
    expected = (MEDIUM.speed_of_sound / (2.0 * math.pi)) * math.sqrt(
        area / (volume * effective_length)
    )

    peaks = _count_peaks(impedance[window])

    return [
        Comparison("Helmholtz tuning fb", measured, expected, 0.005, "Hz"),
        Comparison(
            "port effective length", effective_length, length + 2 * 0.85 * math.sqrt(area / math.pi),
            1e-9, "m",
            note="Two flanged ends at 0.85a each; without this the tuning is 33% high.",
        ),
        Comparison(
            "impedance peaks", peaks, 2, 0, absolute=True,
            note="A vented box has two, either side of fb. One would mean the port is "
                 "not resonating and the minimum found above is not a tuning frequency.",
        ),
    ]


def _count_peaks(magnitude: np.ndarray) -> int:
    """Local maxima in a magnitude curve."""
    rising = np.diff(magnitude) > 0
    return int(np.sum(rising[:-1] & ~rising[1:]))


@case(
    "piston_radiation",
    "Rigid piston in an infinite baffle",
    "Analytic low-frequency limits of the Bessel/Struve radiation impedance",
    tier=1,
)
def piston_radiation():
    """``Z = (rho c/S)[1 - 2J1(2ka)/(2ka) + j 2H1(2ka)/(2ka)]``.

    At low frequency this reduces to ``(rho c/S)[(ka)^2/2 + j 8ka/(3 pi)]`` and at high
    frequency the real part tends to ``rho c/S`` while the imaginary part vanishes. Both
    limits are checked, because they exercise the two ends of the special functions where
    a numerical implementation is most likely to be wrong.
    """
    area = 133e-4
    radius = math.sqrt(area / math.pi)
    element = PistonRadiation("R", area, "Node")
    rho_c_over_s = MEDIUM.density * MEDIUM.speed_of_sound / area

    low = 2.0 * math.pi * 20.0
    k_low = low / MEDIUM.speed_of_sound
    z_low = element.impedance(np.array([low]), MEDIUM)[0]

    high = 2.0 * math.pi * 20000.0
    z_high = element.impedance(np.array([high]), MEDIUM)[0]

    return [
        Comparison(
            "low-frequency resistance", z_low.real / rho_c_over_s, (k_low * radius) ** 2 / 2.0,
            0.01, note="ka = %.4f, well inside the small-argument regime." % (k_low * radius),
        ),
        Comparison(
            "low-frequency reactance", z_low.imag / rho_c_over_s,
            8.0 * k_low * radius / (3.0 * math.pi), 0.01,
        ),
        Comparison(
            "high-frequency resistance", z_high.real / rho_c_over_s, 1.0, 0.02,
            note="Above ka >> 1 the piston radiates as if into an infinite tube.",
        ),
        Comparison(
            "high-frequency reactance", z_high.imag / rho_c_over_s, 0.0, 0.05, absolute=True,
        ),
    ]


@case(
    "shared_back_volume",
    "Two drivers sharing a back volume",
    "Coupled solve vs. independent superposition; equivalence with one driver in half "
    "the volume",
    tier=1,
)
def shared_back_volume():
    """The case that justifies solving the whole network at once (STRUCTURE.md §2.4).

    Two identical drivers sharing a box of volume ``V`` each see the same stiffness as one
    driver alone in ``V/2``: each is pushing against air the other is also compressing.
    That equivalence is exact and independent of this solver, so it is a real check.

    Superposing two separate single-driver runs instead gives the resonance of one driver
    in the *full* volume, which is lower by ``sqrt((1+r)/(1+r/2))``. Both numbers are
    reported, because the size of the discrepancy is the point.
    """
    volume = 20e-3
    solved = {}
    for label, count, box in (("shared", 2, volume), ("equivalent", 1, volume / 2.0)):
        network = Network(MEDIUM)
        for index in range(count):
            network.add(
                Driver(f"D{index}", WOOFER, front_node="Front", back_node="Box", voltage=2.83)
            )
        network.add(Compliance("box", box, "Box"))
        network.add(Compliance("front", FREE_AIR_M3, "Front"))
        # The *system* impedance, with every driver powered, which is what a bench
        # measurement of the finished pair reads. A single branch measured with the other
        # unpowered sees a different, lower resonance -- correctly, since the silent
        # driver's diaphragm is then just another compliance.
        impedance = network.solve(FREQUENCY).system_impedance()
        solved[label] = resonance_of(impedance.frequency, impedance.magnitude)

    network = Network(MEDIUM)
    network.add(Driver("D", WOOFER, front_node="Front", back_node="Box", voltage=2.83))
    network.add(Compliance("box", volume, "Box"))
    network.add(Compliance("front", FREE_AIR_M3, "Front"))
    impedance = network.solve(FREQUENCY).system_impedance()
    superposed = resonance_of(impedance.frequency, impedance.magnitude)

    ratio = WOOFER.Vas(MEDIUM) / volume
    expected_shared = WOOFER.fs * math.sqrt(1.0 + 2.0 * ratio)
    expected_gap = math.sqrt((1.0 + 2.0 * ratio) / (1.0 + ratio))

    return [
        Comparison("two drivers in V, resonance", solved["shared"], expected_shared, 0.005, "Hz"),
        Comparison(
            "equals one driver in V/2", solved["shared"], solved["equivalent"], 1e-6,
            note="Exact equivalence: two cones compressing one volume load each other.",
        ),
        Comparison(
            "error from independent superposition", solved["shared"] / superposed, expected_gap,
            0.005,
            note="Superposition would put the resonance %.1f%% low -- the reason the "
                 "network is solved simultaneously." % ((expected_gap - 1.0) * 100.0),
        ),
    ]


@case(
    "polarity_summation",
    "Two drivers summing at one node",
    "Complex summation: equal sources double the pressure, opposed sources cancel",
    tier=1,
)
def polarity_summation():
    """Reversing one driver's polarity must cancel, not merely reduce.

    Three things are checked, and the middle one is the one that matters.

    In a volume large enough that the drivers do not load each other, doubling the sources
    doubles the pressure: the textbook +6 dB. Shrink the volume to a litre and it is
    **−0.5 dB instead** — adding a driver makes it very slightly *quieter*, because each
    diaphragm is now working against air the other is also driving. That is the whole
    argument of STRUCTURE.md §2.4 in one number, and it is checked against the nodal
    algebra written out by hand: with identical drivers of admittance ``Yd`` on a node of
    admittance ``Yc``, the ratio is ``2(Yd + Yc)/(2Yd + Yc)``, which is a statement about
    the circuit rather than about this solver's matrix assembly.

    And reversing one driver cancels to numerical precision. A magnitude-only pipeline
    would report +6 dB in all three cases and crossover design would be impossible.
    """
    def pressure(count: int, volume: float, inverted: bool = False) -> np.ndarray:
        network = Network(MEDIUM)
        for index in range(count):
            polarity = -1 if (inverted and index == 1) else 1
            network.add(
                Driver(f"D{index}", WOOFER, front_node="Cavity", voltage=2.83, polarity=polarity)
            )
        network.add(Compliance("cavity", volume, "Cavity"))
        return network.solve(FREQUENCY).pressure("Cavity").magnitude

    index = int(np.argmin(np.abs(FREQUENCY - 100.0)))
    omega = 2.0 * math.pi * FREQUENCY[index]

    uncoupled = 20.0 * math.log10(
        pressure(2, FREE_AIR_M3)[index] / pressure(1, FREE_AIR_M3)[index]
    )

    small = 1e-3
    coupled = 20.0 * math.log10(pressure(2, small)[index] / pressure(1, small)[index])
    driver_admittance = 1.0 / Driver("x", WOOFER, front_node="Cavity").impedance(
        np.array([omega]), MEDIUM
    )[0]
    cavity_admittance = 1.0 / Compliance("c", small, "Cavity").impedance(
        np.array([omega]), MEDIUM
    )[0]
    predicted = 20.0 * math.log10(
        abs(
            2.0 * (driver_admittance + cavity_admittance)
            / (2.0 * driver_admittance + cavity_admittance)
        )
    )

    opposed = pressure(2, small, inverted=True)
    one = pressure(1, small)

    return [
        Comparison(
            "uncoupled doubling", uncoupled, 6.0206, 0.02, "dB", absolute=True,
            note="Two drivers into a volume so large they cannot load each other.",
        ),
        Comparison(
            "coupled doubling", coupled, predicted, 1e-9, "dB", absolute=True,
            note="Into 1 litre the same pair gives %+.2f dB, not +6. Mutual loading is "
                 "the point of solving the network simultaneously." % coupled,
        ),
        Comparison(
            "opposed-polarity cancellation", float(opposed.max() / one.max()), 0.0, 1e-12,
            absolute=True,
            note="Identical drivers in antiphase cancel to numerical precision.",
        ),
    ]
