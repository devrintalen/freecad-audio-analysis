#!/usr/bin/env python3
"""What do the rear vents of an open-back headphone actually do?

A worked Tier 1 study, and the question that motivated the driver_cup design review
(STRUCTURE.md §6.7). Runs entirely on the lumped network solver -- no external solver,
no meshing, no 3D.

    python3 examples/open_back_study.py

The model: a driver radiating into the ear cavity at the front, with its rear loaded by
the cup volume, which vents to the outside through an opening of adjustable area and
damping. Sweeping the vent takes the design from fully sealed to fully open and shows what
happens in between.

Numbers here are placeholders in the right region, not measurements of any real driver.
Substitute the real Thiele-Small parameters and the extracted cavity volumes and the
conclusions become specific to the design.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freecad.audio_analysis.checks import report_lumped_validity  # noqa: E402
from freecad.audio_analysis.physics import air  # noqa: E402
from freecad.audio_analysis.physics.driver import DriverParameters  # noqa: E402
from freecad.audio_analysis.physics.network import (  # noqa: E402
    GROUND,
    AcousticMass,
    Compliance,
    Driver,
    Leak,
    Network,
    Resistance,
)
from freecad.audio_analysis.results.curve import ResponseCurve, log_frequencies  # noqa: E402

MEDIUM = air.AirProperties.at()

# A 70 mm headphone driver. Sd for a 70 mm frame is roughly a 58 mm effective diameter.
DRIVER = DriverParameters.from_thiele_small(
    name="70 mm woofer",
    fs=45.0, Re=32.0, Qms=2.5, Qes=0.7,
    Sd=math.pi * (0.058 / 2) ** 2,
    Vas=0.0025,
    Xmax=0.0008,
    medium=MEDIUM,
)

# Circumaural volumes. The pad-to-ear space of an over-ear headphone is on the order of
# 100 cm^3; using an in-ear figure here would make the front cavity so stiff that it
# swamps the rear entirely and the vent would appear to do nothing.
EAR_VOLUME = 1.0e-4      # m^3, air trapped between pad and ear
CUP_VOLUME = 2.0e-4      # m^3, rear cavity behind the diaphragm
CUP_DIMENSION = 0.1056   # m, largest dimension of the cup -- sets lumped validity
SEAL_LEAK = dict(gap=1.5e-4, width=0.35, length=0.004)  # pad leak: always present


def build(vent_area: float, mesh_rayls: float) -> Network:
    """A headphone with a rear vent of the given area and damping."""
    net = Network(MEDIUM)
    net.add(Driver("driver", DRIVER, front_node="EAR", back_node="CUP", voltage=0.1))
    net.add(Compliance("ear", EAR_VOLUME, "EAR"))
    net.add(Leak("seal", node_a="EAR", node_b=GROUND, **SEAL_LEAK))
    net.add(Compliance("cup", CUP_VOLUME, "CUP"))

    if vent_area > 0.0:
        if mesh_rayls > 0.0:
            # A mesh *covering* a vent is in SERIES with it: all the air leaving the cup
            # must pass through both. Wiring it in parallel instead -- an easy slip, and
            # one this study made on its first run -- models a second, separate opening,
            # so a high resistance would then do almost nothing rather than blocking the
            # vent. Topology errors like this are why setup starts from a template (§6.7).
            net.add(AcousticMass("vent", area=vent_area, length=0.003,
                                 node_a="CUP", node_b="VENT"))
            net.add(Resistance.from_rayls("mesh", mesh_rayls, vent_area, "VENT", GROUND))
        else:
            net.add(AcousticMass("vent", area=vent_area, length=0.003,
                                 node_a="CUP", node_b=GROUND))
    return net


#: Frequencies that characterise the shape of a bass response.
PROBE_FREQUENCIES = (30.0, 60.0, 120.0, 300.0)


def peak_of(curve: ResponseCurve) -> tuple[float, float]:
    """Height and frequency of the largest peak within the trusted range.

    Restricted to the trusted portion so a spurious feature above the lumped validity
    limit is never reported as if it were real (§2.4).
    """
    trusted = curve.trusted()
    spl = trusted.spl
    index = int(np.argmax(spl))
    return spl[index], float(trusted.frequency[index])


def main() -> int:
    frequency = log_frequencies(20.0, 2000.0, 48)
    limit = MEDIUM.lumped_validity_limit(CUP_DIMENSION)

    print(f"Driver: {DRIVER.describe()}")
    print(f"Ear volume {EAR_VOLUME * 1e6:.1f} cm3, cup volume {CUP_VOLUME * 1e6:.0f} cm3")
    print(f"Sealed-box resonance in the cup alone: "
          f"{DRIVER.sealed_box_resonance(CUP_VOLUME, MEDIUM):.1f} Hz "
          f"(free air {DRIVER.fs:.1f} Hz)\n")

    header = "  ".join(f"{f:5.0f}Hz" for f in PROBE_FREQUENCIES)
    print(f"Rear vent sweep, undamped -- what opening the back does")
    print(f"  {'vent area':>10}  {header}   peak")
    for area_cm2 in (0.0, 0.5, 2.0, 8.0, 32.0):
        curve = build(area_cm2 * 1e-4, 0.0).solve(frequency, valid_below=limit).pressure("EAR")
        cells = "  ".join(f"{curve.spl_at(f):6.1f} " for f in PROBE_FREQUENCIES)
        peak, at = peak_of(curve)
        label = "sealed" if area_cm2 == 0 else f"{area_cm2:.1f} cm2"
        print(f"  {label:>10}  {cells}  {peak:5.1f} dB @ {at:3.0f} Hz")

    print("\nDamping a 0.5 cm2 vent -- a mesh tames the resonance a small vent creates")
    print(f"  {'mesh':>10}  {header}   peak")
    for rayls in (0.0, 20.0, 100.0, 500.0, 2000.0):
        curve = build(0.5e-4, rayls).solve(frequency, valid_below=limit).pressure("EAR")
        cells = "  ".join(f"{curve.spl_at(f):6.1f} " for f in PROBE_FREQUENCIES)
        peak, at = peak_of(curve)
        label = "none" if rayls == 0 else f"{rayls:.0f} rayl"
        print(f"  {label:>10}  {cells}  {peak:5.1f} dB @ {at:3.0f} Hz")

    print("\nSeal quality at a 8 cm2 vent -- how much bass depends on fit")
    print(f"  {'pad gap':>12}  {'SPL @30Hz':>10}  {'SPL @100Hz':>11}")
    for gap_um in (50, 150, 400, 1000):
        net = Network(MEDIUM)
        net.add(Driver("driver", DRIVER, front_node="EAR", back_node="CUP", voltage=0.1))
        net.add(Compliance("ear", EAR_VOLUME, "EAR"))
        net.add(Leak("seal", gap=gap_um * 1e-6, width=0.35, length=0.004,
                     node_a="EAR", node_b=GROUND))
        net.add(Compliance("cup", CUP_VOLUME, "CUP"))
        net.add(AcousticMass("vent", area=8.0e-4, length=0.003, node_a="CUP", node_b=GROUND))
        curve = net.solve(frequency, valid_below=limit).pressure("EAR")
        print(f"  {gap_um:>9} um  {curve.spl_at(30.0):9.1f} dB  {curve.spl_at(100.0):10.1f} dB")

    print()
    print(report_lumped_validity(CUP_DIMENSION, frequency.max(), MEDIUM).format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
