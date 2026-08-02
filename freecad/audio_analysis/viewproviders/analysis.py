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
        """Nest the analysis's members under it in the tree."""
        return list(getattr(self.Object, "Group", []))
