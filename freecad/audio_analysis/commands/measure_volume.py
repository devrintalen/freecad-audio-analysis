"""The Tier 0 "hello world": measure the enclosed volume of selected solids.

Small, but it exercises the whole path the workbench depends on -- selection, geometry
access, unit conversion, user feedback -- and it is genuinely useful on its own, since
enclosure volume is the dominant parameter in every sealed-box calculation.
"""

from __future__ import annotations

import FreeCAD
import FreeCADGui

from freecad.audio_analysis.commands.base import AudioCommand, register
from freecad.audio_analysis.geometry import measure_volumes


class MeasureVolume(AudioCommand):
    Name = "MeasureVolume"
    MenuText = "Measure enclosed volume"
    ToolTip = (
        "Report the enclosed volume of the selected solids in litres, cubic centimetres "
        "and cubic metres. Enclosure volume drives sealed-box behaviour directly."
    )
    IconName = "MeasureVolume"

    def run(self) -> None:
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            FreeCAD.Console.PrintError(
                "Audio Analysis: select one or more solids to measure.\n"
            )
            return

        measured, problems = measure_volumes(selection)

        for measurement in measured:
            FreeCAD.Console.PrintMessage(f"Audio Analysis: {measurement.describe()}\n")
        for problem in problems:
            FreeCAD.Console.PrintWarning(f"Audio Analysis: {problem}\n")

        if len(measured) > 1:
            total_mm3 = sum(m.volume_mm3 for m in measured)
            total_litres = total_mm3 / 1.0e6
            FreeCAD.Console.PrintMessage(
                f"Audio Analysis: total of {len(measured)} objects: {total_litres:.4g} litre\n"
            )

    def IsActive(self) -> bool:
        return FreeCAD.ActiveDocument is not None and bool(FreeCADGui.Selection.getSelection())


def register_all() -> list[str]:
    return [register(MeasureVolume())]
