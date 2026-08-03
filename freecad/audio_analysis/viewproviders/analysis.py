"""View provider for the analysis container."""

from __future__ import annotations

from typing import Any

import FreeCADGui

from freecad.audio_analysis.viewproviders.base import ViewProviderAudioObject


class ViewProviderAudioAnalysis(ViewProviderAudioObject):
    """Shows the analysis as a group, and makes double-click activate it."""

    IconName = "AudioAnalysis"

    def doubleClicked(self, vobj: Any) -> bool:
        """Make this the active analysis, so new objects land inside it."""
        if FreeCADGui.ActiveDocument is not None:
            FreeCADGui.ActiveDocument.ActiveView.setActiveObject("AudioAnalysis", vobj.Object)
        return True

    def claimChildren(self) -> list[Any]:
        """Only the members nothing else claims.

        Elements are nested under the node they connect to, so listing every member here
        as well would show each of them twice. What is left at this level is the nodes,
        the study objects, and any element whose terminals are all on the exterior --
        which is exactly the set that deserves to be conspicuous.
        """
        from freecad.audio_analysis.objects.network_objects import unclaimed

        return unclaimed(getattr(self.Object, "Group", []))
