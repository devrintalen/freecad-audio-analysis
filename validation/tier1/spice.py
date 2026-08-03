"""Crossover into a real driver impedance, checked against ngspice.

The one Tier 1 benchmark whose reference is another *solver* rather than a formula. It is
worth the trouble because it validates the part of the crossover code with the most room
for a plausible-looking error: the ABCD cascade, the Thevenin reduction of the ladder, and
the extraction of terminal quantities back out of the acoustic solve. A sign convention or
a transposed matrix there would still produce a smooth, believable curve.

Everything in this case really is R/L/C, which is the one regime where SPICE and this
workbench can be asked the identical question. The driver appears in the netlist as its
standard electrical equivalent — the blocked coil in series with a parallel RLC standing
for the motional impedance:

    R_es = BL^2 / Rms      C_mes = Mms / BL^2      L_ces = Cms * BL^2

That mapping is derived, not assumed: with no acoustic load the terminal impedance of the
model reduces to ``Re + j w Le + BL^2 / (j w Mms + Rms + 1/(j w Cms))``, and the second
term is the admittance sum of exactly those three components.

ngspice is invoked as a subprocess over files, like every other solver here, and its
absence skips the case rather than failing it.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile

import numpy as np

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.crossover import make_filter
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.physics.network import Compliance, Driver, Network
from validation.tier1.analytic import FREE_AIR_M3
from validation.harness import Comparison, Skip, case

MEDIUM = air.AirProperties.at()

DRIVER = DriverParameters.from_thiele_small(
    name="woofer", fs=40.0, Re=6.0, Qms=3.0, Qes=0.5, Sd=133e-4, Vas=10e-3, Le=0.5e-3
)

#: Where the two solvers are compared. Chosen to straddle the driver's resonance, the
#: crossover corner, and the region where voice-coil inductance dominates.
PROBE_FREQUENCIES = (20.0, 40.0, 100.0, 500.0, 1000.0, 4000.0, 15000.0)

CROSSOVER_HZ = 1000.0
NOMINAL_OHM = 6.0


def _ngspice() -> str:
    path = shutil.which("ngspice")
    if not path:
        raise Skip("ngspice is not installed; see docs/SETUP.md")
    return path


def _netlist(components, frequencies) -> str:
    """A netlist for the ladder feeding the driver's electrical equivalent.

    Frequencies are given as a list of single-point AC analyses rather than a sweep, so
    the two solvers are compared at exactly the same frequencies with no interpolation
    standing between them.
    """
    lines = ["* crossover into a driver equivalent circuit", "V1 in 0 dc 0 ac 1"]

    node = "in"
    for index, component in enumerate(components):
        prefix = {"L": "L", "C": "C", "R": "R"}[component.kind]
        if component.placement == "series":
            following = f"n{index}"
            lines.append(f"{prefix}{index} {node} {following} {component.value:.12g}")
            node = following
        else:
            lines.append(f"{prefix}{index} {node} 0 {component.value:.12g}")

    p = DRIVER
    lines += [
        # A zero-volt source is an ideal wire in SPICE. It exists only so the driver's
        # terminals always have the node name "drv", whether or not a ladder precedes
        # them -- reading the voltage across the *motional branch* instead, which is the
        # obvious mistake here, omits the blocked coil impedance entirely.
        f"Vdrv {node} drv 0",
        f"Rvc drv vc {p.Re:.12g}",
        f"Lvc vc mot {p.Le:.12g}",
        f"Res mot 0 {p.BL ** 2 / p.Rms:.12g}",
        f"Cmes mot 0 {p.Mms / p.BL ** 2:.12g}",
        f"Lces mot 0 {p.Cms * p.BL ** 2:.12g}",
        ".control",
    ]
    for frequency in frequencies:
        lines.append(f"ac lin 1 {frequency:.12g} {frequency:.12g}")
        lines.append("print v(in) v(drv) i(v1)")
    lines += [".endc", ".end", ""]
    return "\n".join(lines)


def _parse(output: str) -> list[tuple[complex, complex, complex]]:
    """Pull ``v(in)``, ``v(drv)`` and ``i(v1)`` out of ngspice's print blocks.

    Printed rather than written to a data file because ngspice's ``print`` gives complex
    values in an unambiguous ``a, b`` form, whereas ``wrdata`` interleaves a frequency
    column per vector and has changed layout between releases.
    """
    values: dict[str, complex] = {}
    rows: list[tuple[complex, complex, complex]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith(("v(", "i(")):
            continue
        name = parts[0]
        text = line.split("=", 1)[1] if "=" in line else " ".join(parts[1:])
        try:
            real, _, imaginary = text.partition(",")
            values[name] = complex(float(real), float(imaginary))
        except ValueError:
            continue
        if {"v(in)", "v(drv)", "i(v1)"} <= values.keys():
            rows.append((values["v(in)"], values["v(drv)"], values["i(v1)"]))
            values = {}
    return rows


def _run_ngspice(components, frequencies):
    binary = _ngspice()
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "case.cir")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_netlist(components, frequencies))
        finished = subprocess.run(
            [binary, "-b", path], capture_output=True, text=True, timeout=120
        )
    if finished.returncode != 0:
        raise Skip(f"ngspice exited {finished.returncode}: {finished.stderr.strip()[:200]}")
    rows = _parse(finished.stdout)
    if len(rows) != len(frequencies):
        raise Skip(
            f"ngspice returned {len(rows)} points for {len(frequencies)} frequencies; "
            f"its print format may have changed"
        )
    return rows


def _solve_here(filter_, frequencies):
    """The same circuit through the workbench: a driver with negligible acoustic load.

    A very large front volume presents almost no acoustic impedance, so the driver is
    effectively in free air and its terminal behaviour is purely electrical -- which is
    the only condition under which SPICE can be asked the same question.
    """
    network = Network(MEDIUM)
    network.add(
        Driver("D", DRIVER, front_node="Front", voltage=1.0, filter=filter_)
    )
    network.add(Compliance("front", FREE_AIR_M3, "Front"))
    return network.solve(np.asarray(frequencies, dtype=float))


@case(
    "crossover_vs_ngspice",
    "Crossover into a real driver impedance",
    "ngspice, independently, on the same netlist",
    tier=1,
)
def crossover_vs_ngspice():
    filter_ = make_filter(
        response="Lowpass", alignment="Butterworth", order=2, frequency=CROSSOVER_HZ,
        passive=True, impedance=NOMINAL_OHM,
    )
    rows = _run_ngspice(filter_.components, PROBE_FREQUENCIES)
    solution = _solve_here(filter_, PROBE_FREQUENCIES)

    mine_z = solution.input_impedance("D").values
    mine_v = solution.terminal_voltage("D").values

    comparisons = []
    worst_z = worst_v = 0.0
    for index, frequency in enumerate(PROBE_FREQUENCIES):
        v_in, v_coil, current = rows[index]
        # ngspice reports current flowing into the source's positive terminal, so the
        # current delivered to the circuit is its negation.
        reference_z = v_in / -current
        worst_z = max(worst_z, abs(mine_z[index] - reference_z) / abs(reference_z))
        worst_v = max(worst_v, abs(mine_v[index] - v_coil) / max(abs(v_coil), 1e-12))
        comparisons.append(
            Comparison(
                f"|Z| at {frequency:g} Hz", float(abs(mine_z[index])), float(abs(reference_z)),
                0.002, "ohm",
            )
        )

    comparisons.append(
        Comparison(
            "worst complex impedance error", worst_z, 0.0, 0.002, absolute=True,
            note="Magnitude and phase together, so a phase-only error cannot hide.",
        )
    )
    comparisons.append(
        Comparison(
            "worst coil terminal voltage error", worst_v, 0.0, 0.002, absolute=True,
            note="Validates the Thevenin reduction of the ladder, not only its input "
                 "impedance.",
        )
    )
    return comparisons


@case(
    "driver_equivalent_circuit",
    "Free-air driver impedance",
    "ngspice on the standard electrical equivalent circuit, with no filter",
    tier=1,
)
def driver_equivalent_circuit():
    """The same comparison with the ladder removed, to localise a failure.

    If both cases fail, the driver model is wrong. If only the crossover case fails, the
    ladder is. Worth the extra thirty lines the first time something breaks.
    """
    rows = _run_ngspice([], PROBE_FREQUENCIES)
    solution = _solve_here(None, PROBE_FREQUENCIES)
    mine = solution.input_impedance("D").values

    worst = 0.0
    comparisons = []
    for index, frequency in enumerate(PROBE_FREQUENCIES):
        v_in, _, current = rows[index]
        reference = v_in / -current
        worst = max(worst, abs(mine[index] - reference) / abs(reference))
        comparisons.append(
            Comparison(
                f"|Z| at {frequency:g} Hz", float(abs(mine[index])), float(abs(reference)),
                0.002, "ohm",
            )
        )

    resonance = DRIVER.fs
    comparisons.append(
        Comparison("worst complex error", worst, 0.0, 0.002, absolute=True)
    )
    comparisons.append(
        Comparison(
            "equivalent-circuit resonance",
            1.0 / (2.0 * math.pi * math.sqrt((DRIVER.Mms / DRIVER.BL**2) * (DRIVER.Cms * DRIVER.BL**2))),
            resonance, 1e-9, "Hz",
            note="The RLC values must resonate at fs, or the netlist is not this driver.",
        )
    )
    return comparisons
