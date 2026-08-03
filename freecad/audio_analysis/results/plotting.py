"""Plotting results with matplotlib.

Kept free of FreeCADGui so it can be exercised headlessly with a non-interactive backend;
the GUI command simply calls :func:`plot_solution` and lets matplotlib open its window.

The one non-obvious rule: **the region beyond a curve's validity limit is greyed and
annotated, never drawn as if it were trustworthy** (STRUCTURE.md §6.8). A confident line
running to 20 kHz from a model valid to 400 Hz is the easiest way for this tool to
mislead, and a plot is where that would happen.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from freecad.audio_analysis.results.curve import ResponseCurve

#: Colour for the shaded region above a validity limit.
INVALID_SHADE = "#c0392b"


def _shade_invalid(axis: Any, curve: ResponseCurve, annotate: bool = True) -> None:
    """Grey out the part of the axis beyond the curve's validity limit."""
    limit = curve.valid_below
    if limit is None or limit >= curve.frequency.max():
        return
    axis.axvspan(limit, curve.frequency.max(), color=INVALID_SHADE, alpha=0.12, zorder=0)
    axis.axvline(limit, color=INVALID_SHADE, linestyle="--", linewidth=1.2, zorder=1)
    if annotate:
        axis.annotate(
            f"lumped model invalid above {limit:.0f} Hz",
            xy=(limit, 0.02), xycoords=("data", "axes fraction"),
            xytext=(4, 4), textcoords="offset points",
            color=INVALID_SHADE, fontsize=8, rotation=90, va="bottom",
        )


def plot_curves(
    curves: Sequence[ResponseCurve],
    axis: Any = None,
    *,
    title: str = "",
    smoothing: int | None = None,
) -> Any:
    """Plot pressure curves as SPL against a logarithmic frequency axis."""
    import matplotlib.pyplot as plt

    if not curves:
        raise ValueError("nothing to plot")
    if axis is None:
        _, axis = plt.subplots(figsize=(9, 5))

    for curve in curves:
        shown = curve.smooth(smoothing) if smoothing else curve
        axis.semilogx(shown.frequency, shown.spl, label=shown.label or curve.quantity)

    axis.set_xlabel("Frequency (Hz)")
    axis.set_ylabel("SPL (dB re 20 µPa)")
    axis.grid(True, which="both", alpha=0.25)
    axis.legend(loc="best", fontsize=8)
    if title:
        axis.set_title(title)
    _shade_invalid(axis, curves[0])
    return axis


def plot_contributions(contributions: dict, *, show: bool = True, title: str = "") -> Any:
    """Each driver's share of a node's pressure, with their sum.

    The plot crossover work is done against. The sum is drawn heavy and the individual
    drivers light, because the question being asked is where one hands over to the other
    and whether the handover adds or cancels — and a cancellation shows up as the sum
    dipping *below* both contributors, which is only visible when they are on one axis.
    """
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 5.5))
    for name, curve in contributions.items():
        heavy = name == "sum"
        axis.semilogx(
            curve.frequency, curve.spl, label=name,
            linewidth=2.4 if heavy else 1.2, color="black" if heavy else None,
            zorder=3 if heavy else 2,
        )
    axis.set_xlabel("Frequency (Hz)")
    axis.set_ylabel("SPL (dB re 20 µPa)")
    axis.set_title(title or "Driver contributions")
    axis.grid(True, which="both", alpha=0.25)
    axis.legend(loc="best", fontsize=8)
    _shade_invalid(axis, next(iter(contributions.values())))

    figure.tight_layout()
    if show:
        plt.show()
    return figure


def plot_family(family: Any, *, show: bool = True, smoothing: int | None = None) -> Any:
    """Overlay a swept family, with a delta panel underneath.

    Two panels, because the absolute view and the delta view answer different questions.
    The overlay says what each setting sounds like; the delta says what *changing* the
    setting did, and a 2 dB shift buried in a 40 dB roll-off is invisible on the first and
    unmistakable on the second (STRUCTURE.md §6.9).

    Without a reference member only the overlay is drawn -- a delta plot with nothing to
    subtract would be a panel of zeroes pretending to be information.
    """
    import matplotlib.pyplot as plt

    has_deltas = family.baseline() is not None
    rows = 2 if has_deltas else 1
    figure, axes = plt.subplots(rows, 1, figsize=(10, 4.5 * rows), sharex=True, squeeze=False)
    overlay = axes[0][0]

    for label, curve in zip(family.labels, family.curves):
        shown = curve.smooth(smoothing) if smoothing else curve
        values = shown.spl if family.quantity == "pressure" else shown.magnitude
        overlay.semilogx(shown.frequency, values, label=label)
    overlay.set_ylabel(
        "SPL (dB re 20 µPa)" if family.quantity == "pressure" else family.curves[0].unit
    )
    overlay.set_title(f"{family.parameter} — {len(family)} runs")
    overlay.grid(True, which="both", alpha=0.25)
    overlay.legend(loc="best", fontsize=8, title=family.parameter)
    _shade_invalid(overlay, family.curves[0])

    if has_deltas:
        delta_axis = axes[1][0]
        for label, delta in zip(family.labels, family.deltas()):
            delta_axis.semilogx(family.frequency, delta, label=label)
        delta_axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.6)
        delta_axis.set_ylabel(f"Change vs {family.labels[family.reference]} (dB)")
        delta_axis.set_title("What the change did")
        delta_axis.grid(True, which="both", alpha=0.25)
        _shade_invalid(delta_axis, family.curves[0], False)
        delta_axis.set_xlabel("Frequency (Hz)")
    else:
        overlay.set_xlabel("Frequency (Hz)")

    figure.tight_layout()
    if show:
        plt.show()
    return figure


def plot_solution(solution: Any, analysis: Any = None, *, show: bool = True) -> Any:
    """A four-panel overview of a solved network: SPL, impedance, excursion, group delay.

    Returns the figure so callers can save it instead of showing it.
    """
    import matplotlib.pyplot as plt

    network = solution.network
    figure, axes = plt.subplots(2, 2, figsize=(12, 8))
    (spl_axis, impedance_axis), (excursion_axis, delay_axis) = axes

    # --- SPL at every node
    pressures = []
    for node in network.node_names():
        curve = solution.pressure(node)
        if analysis is not None:
            from freecad.audio_analysis.builder import label_for_node

            curve = type(curve)(
                curve.frequency, curve.values, quantity=curve.quantity, unit=curve.unit,
                label=label_for_node(analysis, node), valid_below=curve.valid_below,
                metadata=curve.metadata,
            )
        pressures.append(curve)
    if pressures:
        plot_curves(pressures, spl_axis, title="Sound pressure level")

    # --- Electrical impedance
    for driver in network.drivers:
        curve = solution.input_impedance(driver.name)
        impedance_axis.semilogx(curve.frequency, curve.magnitude, label=driver.name)
    impedance_axis.set_xlabel("Frequency (Hz)")
    impedance_axis.set_ylabel("|Z| (ohm)")
    impedance_axis.set_title("Electrical impedance")
    impedance_axis.grid(True, which="both", alpha=0.25)
    impedance_axis.legend(loc="best", fontsize=8)
    if network.drivers:
        _shade_invalid(impedance_axis, solution.input_impedance(network.drivers[0].name), False)

    # --- Excursion, against Xmax
    for driver in network.drivers:
        curve = solution.excursion(driver.name)
        excursion_axis.loglog(curve.frequency, curve.magnitude * 1000.0, label=driver.name)
        excursion_axis.axhline(
            driver.parameters.Xmax * 1000.0, color=INVALID_SHADE, linestyle=":",
            linewidth=1.2, label=f"{driver.name} Xmax",
        )
    excursion_axis.set_xlabel("Frequency (Hz)")
    excursion_axis.set_ylabel("Excursion (mm peak)")
    excursion_axis.set_title("Diaphragm excursion")
    excursion_axis.grid(True, which="both", alpha=0.25)
    excursion_axis.legend(loc="best", fontsize=8)
    if network.drivers:
        _shade_invalid(excursion_axis, solution.excursion(network.drivers[0].name), False)

    # --- Group delay at the first node
    if pressures:
        delay_axis.semilogx(
            pressures[0].frequency, pressures[0].group_delay * 1000.0, label=pressures[0].label
        )
        delay_axis.set_xlabel("Frequency (Hz)")
        delay_axis.set_ylabel("Group delay (ms)")
        delay_axis.set_title("Group delay")
        delay_axis.grid(True, which="both", alpha=0.25)
        delay_axis.legend(loc="best", fontsize=8)
        _shade_invalid(delay_axis, pressures[0], False)

    figure.tight_layout()
    if show:
        plt.show()
    return figure
