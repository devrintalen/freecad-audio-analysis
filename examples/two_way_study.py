#!/usr/bin/env python3
"""What does a crossover do in a two-way over-ear headphone?

The companion to ``open_back_study.py``, and the second half of the driver_cup design
question. Runs entirely on the lumped network solver -- no external solver, no meshing.

    python3 examples/two_way_study.py

Three things are demonstrated, in order of how surprising they are:

1. **Two drivers in one ear cavity are not two headphones.** They share the air, so each
   one's motion changes the pressure the other works against. Solving them separately and
   adding the curves gives a different answer, and the difference is largest exactly in
   the crossover region.
2. **Polarity is not a detail, and the textbook rule is not the whole story.** With
   matched drivers a second-order pair wired in phase cancels into a 48 dB notch and a
   fourth-order pair does not -- exactly as the rule says. With *these* drivers the rule
   comes out backwards, because each contributes phase of its own through the crossover
   region. The workbench warns either way; the solve is what settles it.
3. **A passive crossover is not its own transfer function.** The ladder is loaded by the
   driver's impedance, which is nothing like the flat resistance its component values
   assumed, and it changes the driver's damping as well as its level.

And one thing that is not demonstrated, because it cannot be: a 2.5 kHz crossover in a
105 mm cup sits far above the frequency where a lumped model of that cup means anything.
The script says so at the end, with the number.

Values here are plausible for their class, not measurements of any real driver.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.devpath import setup  # noqa: E402

setup()

from freecad.audio_analysis.checks import report_lumped_validity  # noqa: E402
from freecad.audio_analysis.physics import air  # noqa: E402
from freecad.audio_analysis.physics.crossover import make_filter  # noqa: E402
from freecad.audio_analysis.physics.driver import DriverParameters  # noqa: E402
from freecad.audio_analysis.physics.network import (  # noqa: E402
    Compliance,
    Driver,
    Leak,
    Network,
)

MEDIUM = air.AirProperties.at()

EAR_CAVITY_M3 = 100e-6
CUP_CAVITY_M3 = 200e-6
TWEETER_CHAMBER_M3 = 3e-6
CUP_DIAMETER_M = 0.1056
CROSSOVER_HZ = 2500.0
DRIVE_V = 0.1

FREQUENCY = np.logspace(math.log10(20.0), math.log10(20000.0), 600)

WOOFER = DriverParameters.from_thiele_small(
    name="woofer", fs=45.0, Re=32.0, Qms=2.5, Qes=0.7, Sd=26.4e-4, Vas=2.5e-3, Xmax=0.8e-3
)
TWEETER = DriverParameters.from_thiele_small(
    name="tweeter", fs=1200.0, Re=32.0, Qms=2.0, Qes=0.8, Sd=3.0e-4, Vas=0.02e-3, Xmax=0.2e-3
)


def build(order: int, invert_tweeter: bool = False, passive: bool = False) -> Network:
    """The two-way topology: shared ear cavity, separate rear loading."""
    network = Network(MEDIUM)

    low = make_filter(
        response="Lowpass", alignment="Linkwitz-Riley", order=order,
        frequency=CROSSOVER_HZ, passive=passive, impedance=WOOFER.Re,
    )
    high = make_filter(
        response="Highpass", alignment="Linkwitz-Riley", order=order,
        frequency=CROSSOVER_HZ, gain_db=-4.0, passive=passive, impedance=TWEETER.Re,
    )

    network.add(
        Driver("Woofer", WOOFER, front_node="Ear", back_node="Cup",
               voltage=DRIVE_V, filter=low)
    )
    network.add(
        Driver("Tweeter", TWEETER, front_node="Ear", back_node="Chamber",
               voltage=DRIVE_V, filter=high, polarity=-1 if invert_tweeter else 1)
    )
    network.add(Compliance("ear", EAR_CAVITY_M3, "Ear"))
    network.add(Compliance("cup", CUP_CAVITY_M3, "Cup"))
    network.add(Compliance("chamber", TWEETER_CHAMBER_M3, "Chamber"))
    # A pad seal, because no headphone has none and it dominates the bass.
    network.add(Leak("seal", gap=0.15e-3, width=0.35, length=4e-3, node_a="Ear"))
    return network


def at(curve, frequency: float) -> float:
    return curve.spl[int(np.argmin(np.abs(curve.frequency - frequency)))]


def heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def superposition_error(shared_back: bool) -> tuple[np.ndarray, np.ndarray]:
    """How wrong two independent single-driver runs are, added together.

    The comparison the coupled solve exists to beat. Each driver is solved in its own copy
    of the model, as a single-driver tool would have to, and the two pressures are added.
    """
    tweeter_back = "Cup" if shared_back else "Chamber"
    tweeter_volume = CUP_CAVITY_M3 if shared_back else TWEETER_CHAMBER_M3

    coupled_network = Network(MEDIUM)
    coupled_network.add(
        Driver("Woofer", WOOFER, front_node="Ear", back_node="Cup", voltage=DRIVE_V,
               filter=make_filter(response="Lowpass", alignment="Linkwitz-Riley",
                                  order=4, frequency=CROSSOVER_HZ))
    )
    coupled_network.add(
        Driver("Tweeter", TWEETER, front_node="Ear", back_node=tweeter_back,
               voltage=DRIVE_V,
               filter=make_filter(response="Highpass", alignment="Linkwitz-Riley",
                                  order=4, frequency=CROSSOVER_HZ, gain_db=-4.0))
    )
    coupled_network.add(Compliance("ear", EAR_CAVITY_M3, "Ear"))
    coupled_network.add(Compliance("cup", CUP_CAVITY_M3, "Cup"))
    if not shared_back:
        coupled_network.add(Compliance("chamber", TWEETER_CHAMBER_M3, "Chamber"))
    coupled_network.add(Leak("seal", gap=0.15e-3, width=0.35, length=4e-3, node_a="Ear"))
    coupled = coupled_network.solve(FREQUENCY).pressure("Ear").values

    superposed = np.zeros_like(coupled)
    for name, parameters, back, volume, response, gain in (
        ("Woofer", WOOFER, "Cup", CUP_CAVITY_M3, "Lowpass", 0.0),
        ("Tweeter", TWEETER, tweeter_back, tweeter_volume, "Highpass", -4.0),
    ):
        alone = Network(MEDIUM)
        alone.add(
            Driver(name, parameters, front_node="Ear", back_node=back, voltage=DRIVE_V,
                   filter=make_filter(response=response, alignment="Linkwitz-Riley",
                                      order=4, frequency=CROSSOVER_HZ, gain_db=gain))
        )
        alone.add(Compliance("ear", EAR_CAVITY_M3, "Ear"))
        alone.add(Compliance("back", volume, back))
        alone.add(Leak("seal", gap=0.15e-3, width=0.35, length=4e-3, node_a="Ear"))
        superposed = superposed + alone.solve(FREQUENCY).pressure("Ear").values

    return coupled, 20.0 * np.log10(np.abs(superposed) / np.abs(coupled))


def show_coupling() -> None:
    heading("1. How much do the drivers load each other?")

    for label, shared in (("tweeter in its own chamber", False), ("tweeter sharing the cup", True)):
        _, error = superposition_error(shared)
        worst = int(np.argmax(np.abs(error)))
        print(
            f"  {label:28}  superposition is worst by {error[worst]:+.2f} dB "
            f"at {FREQUENCY[worst]:.0f} Hz"
        )

    print(
        "\n  Worth reading carefully, because the honest answer is 'it depends'.\n"
        "\n  As drawn, the coupling error is only about half a decibel. That is not the\n"
        "  model being lenient -- it is the design being sensible. The tweeter has nine\n"
        "  times less cone area than the woofer and its own sealed chamber, so there is\n"
        "  very little for the two to fight over. Give them a shared cup and the error\n"
        "  jumps five-fold, to a couple of decibels around 500 Hz -- inside the range this\n"
        "  model can be trusted, and large enough to matter.\n"
        "\n  So the tweeter chamber is doing real work, and the number above is what it is\n"
        "  worth. A single-driver tool cannot produce either figure, because it cannot\n"
        "  represent the question (STRUCTURE.md 2.4)."
    )


def matched_pair(order: int, invert: bool) -> float:
    """The same crossover feeding two *identical* drivers, for the textbook case."""
    network = Network(MEDIUM)
    for name, response, polarity in (
        ("A", "Lowpass", 1), ("B", "Highpass", -1 if invert else 1)
    ):
        network.add(
            Driver(name, WOOFER, front_node="Ear", back_node="Cup", voltage=DRIVE_V,
                   polarity=polarity,
                   filter=make_filter(response=response, alignment="Linkwitz-Riley",
                                      order=order, frequency=CROSSOVER_HZ))
        )
    network.add(Compliance("ear", EAR_CAVITY_M3, "Ear"))
    network.add(Compliance("cup", CUP_CAVITY_M3, "Cup"))
    network.add(Leak("seal", gap=0.15e-3, width=0.35, length=4e-3, node_a="Ear"))
    return at(network.solve(FREQUENCY).pressure("Ear"), CROSSOVER_HZ)


def show_polarity() -> None:
    heading("2. Polarity, at second order and at fourth")

    print("  Matched drivers -- the textbook case:")
    for order in (2, 4):
        normal, flipped = matched_pair(order, False), matched_pair(order, True)
        print(
            f"    LR{order}: in phase {normal:6.1f} dB, inverted {flipped:6.1f} dB  ->  "
            f"wire the second driver {'inverted' if flipped > normal else 'in phase'}"
        )
    print(
        "\n  Exactly as the rule says: an Nth-order filter rotates the pair by N quarter\n"
        "  turns, so LR2 needs one driver reversed and LR4 does not. Nothing in a parts\n"
        "  list distinguishes the two, and getting it wrong is a deep notch where both\n"
        "  drivers are working hardest."
    )

    print("\n  This design's actual woofer and tweeter:")
    for order in (2, 4):
        normal = at(build(order).solve(FREQUENCY).pressure("Ear"), CROSSOVER_HZ)
        flipped = at(
            build(order, invert_tweeter=True).solve(FREQUENCY).pressure("Ear"), CROSSOVER_HZ
        )
        print(
            f"    LR{order}: in phase {normal:6.1f} dB, inverted {flipped:6.1f} dB  ->  "
            f"wire the tweeter {'inverted' if flipped > normal else 'in phase'}"
        )
    print(
        "\n  The rule comes out backwards, and it is the rule that is wrong, not the model.\n"
        "  The textbook version assumes both drivers are flat and in phase through the\n"
        "  crossover region. These are not: at 2.5 kHz the woofer is far above its own\n"
        "  resonance and the tweeter is only just above its, so each contributes phase of\n"
        "  its own on top of the filter's, and the two rotations do not cancel.\n"
        "\n  The workbench still warns when a pair departs from the textbook expectation,\n"
        "  because that is the right default and an unconsidered polarity is nearly always\n"
        "  a mistake. But the warning is a prompt to check, not a verdict -- and this is\n"
        "  the check. Note also that the difference here is under 2 dB rather than the\n"
        "  nearly 50 dB the matched pair shows: mismatched levels cannot fully cancel, so\n"
        "  a real polarity error usually sounds like a dull midrange, not like silence."
    )


def show_passive() -> None:
    heading("3. A passive ladder is not its own transfer function")

    active = build(order=2).solve(FREQUENCY).pressure("Ear")
    passive = build(order=2, passive=True).solve(FREQUENCY).pressure("Ear")

    low = make_filter(
        response="Lowpass", alignment="Linkwitz-Riley", order=2, frequency=CROSSOVER_HZ,
        passive=True, impedance=WOOFER.Re,
    )
    print(f"  Woofer branch parts: {low.describe()}")
    print(f"  {'Hz':>7}  {'active':>8}  {'passive':>9}  {'difference':>11}")
    for frequency in (50.0, 500.0, CROSSOVER_HZ, 10000.0):
        print(
            f"  {frequency:7.0f}  {at(active, frequency):7.1f} dB  "
            f"{at(passive, frequency):8.1f} dB  "
            f"{at(passive, frequency) - at(active, frequency):+10.1f} dB"
        )
    print(
        "\n  Same nominal alignment, same corner frequency, different result. The ladder\n"
        "  sees the driver's impedance, not the flat resistance its values assumed, and it\n"
        "  sits between the amplifier and the coil so it changes the damping too."
    )


def show_validity() -> None:
    heading("What of this can be believed")

    finding = report_lumped_validity(CUP_DIAMETER_M, FREQUENCY.max(), MEDIUM)
    print(finding.format())
    limit = MEDIUM.lumped_validity_limit(CUP_DIAMETER_M)
    print(
        f"\n  The crossover is at {CROSSOVER_HZ:.0f} Hz and the limit is {limit:.0f} Hz, so\n"
        f"  everything in sections 2 and 3 above is happening in the region this model\n"
        f"  cannot represent. Read them as *comparisons between candidates*, which stay\n"
        f"  useful, and not as predictions of the summed response, which they are not.\n"
        f"  Section 1 is different: driver coupling is a low-frequency effect too, and\n"
        f"  below {limit:.0f} Hz that part of the answer is trustworthy.\n"
        f"\n  The crossover region itself needs Tier 2 or 3."
    )


def main() -> None:
    print(__doc__.split("\n\n")[0])
    print(
        f"\nAir: {air.to_celsius(MEDIUM.temperature):.1f} C, "
        f"{MEDIUM.pressure:.0f} Pa, c = {MEDIUM.speed_of_sound:.1f} m/s"
    )
    show_coupling()
    show_polarity()
    show_passive()
    show_validity()


if __name__ == "__main__":
    main()
