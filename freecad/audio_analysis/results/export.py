"""Writing results to disk.

Results are recomputed on demand rather than stored in the ``.FCStd`` (see
``objects/study.py``), so export is how a curve outlives the session. Two formats, for two
different reasons:

* **CSV** — readable, diffable, opens as a chart in any spreadsheet, and carries the
  provenance header. This is the archive format.
* **FRD** — the plain-text convention loudspeaker tools read, so a response can leave this
  workbench for a crossover simulator or an enclosure program (STRUCTURE.md §6.9). Only
  pressure curves have an FRD form.

Every file carries its provenance in comment lines: the medium, the solver, and the
frequency above which the result stops being trustworthy. A curve that cannot say where it
came from is not evidence, and six months later nobody remembers which run produced which
file.
"""

from __future__ import annotations

import os
import re
from typing import Any, Sequence

from freecad.audio_analysis.results.curve import ResponseCurve

#: Characters that make a file name awkward on some platform or other.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(text: str) -> str:
    """A file-name fragment from a label. Never empty."""
    cleaned = _UNSAFE.sub("_", text.strip()).strip("_")
    return cleaned or "result"


def write_curve(directory: str, stem: str, curve: ResponseCurve) -> list[str]:
    """Write one curve as CSV, plus FRD when it is a pressure."""
    written: list[str] = []
    csv_path = os.path.join(directory, f"{stem}.csv")
    curve.to_csv(csv_path)
    written.append(csv_path)
    if curve.quantity == "pressure":
        frd_path = os.path.join(directory, f"{stem}.frd")
        curve.to_frd(frd_path)
        written.append(frd_path)
    return written


def export_solution(directory: str, solution: Any, analysis: Any = None) -> list[str]:
    """Write every node pressure, driver impedance and excursion from one solve."""
    written: list[str] = []
    network = solution.network

    for node in network.node_names():
        label = node
        if analysis is not None:
            from freecad.audio_analysis.builder import label_for_node

            label = label_for_node(analysis, node)
        written += write_curve(directory, f"pressure_{safe_name(label)}", solution.pressure(node))

    for driver in network.drivers:
        stem = safe_name(driver.name)
        written += write_curve(
            directory, f"impedance_{stem}", solution.input_impedance(driver.name)
        )
        written += write_curve(directory, f"excursion_{stem}", solution.excursion(driver.name))

    if len(network.drivers) > 1:
        # The curve the finished product presents at its plug, which is not any one
        # driver's impedance and is the one an amplifier has to survive.
        written += write_curve(directory, "impedance_system", solution.system_impedance())

    return written


def export_family(directory: str, family: Any) -> list[str]:
    """Write one swept family as a single wide CSV, one column per run."""
    path = os.path.join(directory, f"sweep_{safe_name(family.parameter)}.csv")
    family.to_csv(path)
    return [path]


def export_all(
    directory: str,
    solution: Any = None,
    families: Sequence[Any] = (),
    analysis: Any = None,
) -> list[str]:
    """Write everything available, returning the paths written.

    Raises rather than writing a partial set if the directory is unusable -- a silent
    half-export is worse than none, because the missing files look like curves that were
    never produced.
    """
    if not directory or not os.path.isdir(directory):
        raise ValueError(f"{directory!r} is not a directory")

    written: list[str] = []
    if solution is not None:
        written += export_solution(directory, solution, analysis)
    for family in families:
        written += export_family(directory, family)
    return written
