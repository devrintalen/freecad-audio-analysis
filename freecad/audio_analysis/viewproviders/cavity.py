"""View provider for the extracted cavity.

Displayed translucent and tinted, because a cavity is air: it occupies the same space as
the parts around it, and a solid opaque body there would hide the geometry it was derived
from. Being able to see the cavity *inside* the model is how a user confirms the
extraction found what they meant.
"""

from __future__ import annotations

from typing import Any

from freecad.audio_analysis.viewproviders.base import ViewProviderAudioObject

#: Pale blue, matching the workbench's air colour.
AIR_COLOUR = (0.36, 0.78, 0.96)
AIR_TRANSPARENCY = 70


class ViewProviderAcousticCavity(ViewProviderAudioObject):
    IconName = "Cavity"

    def attach(self, vobj: Any) -> None:
        super().attach(vobj)
        try:
            vobj.ShapeColor = AIR_COLOUR
            vobj.Transparency = AIR_TRANSPARENCY
            vobj.DisplayMode = "Shaded"
        except Exception:  # noqa: BLE001 -- display properties vary between builds
            pass

    # -- editing ---------------------------------------------------------------------
    #
    # Double-clicking a cavity reopens the panel it was made in, which is what every other
    # feature in FreeCAD does. It matters more than usual here: the seed is a pick, and a
    # pick is not something anyone can retype into the property editor.

    def doubleClicked(self, vobj: Any) -> bool:
        import FreeCADGui

        FreeCADGui.ActiveDocument.setEdit(vobj.Object)
        return True

    def setEdit(self, vobj: Any, mode: int = 0) -> bool:
        import FreeCADGui

        from freecad.audio_analysis.taskpanels.cavity_panel import CavityTaskPanel

        if mode != 0 or FreeCADGui.Control.activeDialog():
            return False

        obj = vobj.Object
        seed = None
        reference = getattr(obj, "Seed", None)
        if reference and reference[1]:
            seed = (reference[0], reference[1][0])

        # Opened here rather than in the panel so that Cancel reverts every property the
        # panel touched, not merely the ones it remembered to put back.
        obj.Document.openTransaction("Edit cavity")
        try:
            FreeCADGui.Control.showDialog(CavityTaskPanel(obj, seed=seed))
        except Exception:
            obj.Document.abortTransaction()
            raise
        return True

    def unsetEdit(self, vobj: Any, mode: int = 0) -> bool:
        return True
