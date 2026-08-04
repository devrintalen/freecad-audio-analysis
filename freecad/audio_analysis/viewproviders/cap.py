"""View provider for a cap.

Tinted amber and partly transparent, so a cap never reads as part of the design. It is a
modelling aid that exists to close the fluid domain, and someone looking at the assembly
should be able to tell at a glance which solids they drew and which the analysis added.
Distinct from the cavity's pale blue, which is air.
"""

from __future__ import annotations

from typing import Any

from freecad.audio_analysis.viewproviders.base import ViewProviderAudioObject

#: Amber, deliberately unlike both the parts and the cavity's air blue.
CAP_COLOUR = (0.95, 0.68, 0.25)
CAP_TRANSPARENCY = 40


class ViewProviderAcousticCap(ViewProviderAudioObject):
    IconName = "Cap"

    def attach(self, vobj: Any) -> None:
        super().attach(vobj)
        try:
            vobj.ShapeColor = CAP_COLOUR
            vobj.Transparency = CAP_TRANSPARENCY
            vobj.DisplayMode = "Shaded"
        except Exception:  # noqa: BLE001 -- display properties vary between builds
            pass

    def claimChildren(self) -> list:
        """A cap owns nothing; its source part stays where the user put it."""
        return []
