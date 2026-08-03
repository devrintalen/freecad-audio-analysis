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
