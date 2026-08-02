"""View provider for lumped-network objects.

One provider serves them all, picking its icon from the proxy's Type so a new element
needs only an icon file, not a new class.
"""

from __future__ import annotations

from typing import Any

from freecad.audio_analysis.viewproviders.base import ViewProviderAudioObject

#: Object Type -> icon basename in resources/icons.
ICONS = {
    "Audio::Node": "Node",
    "Audio::Volume": "Volume",
    "Audio::Driver": "Driver",
    "Audio::Port": "Port",
    "Audio::Resistance": "Resistance",
    "Audio::Leak": "Leak",
    "Audio::Radiation": "Radiation",
    "Audio::PassiveRadiator": "Volume",
    "Audio::FrequencySweep": "Sweep",
    "Audio::SolverLumped": "Solve",
}


class ViewProviderNetworkObject(ViewProviderAudioObject):
    """Icon chosen from the object's Type."""

    IconName = "AudioAnalysis"

    def getIcon(self) -> str:
        from freecad.audio_analysis import icon

        object_type = getattr(getattr(self, "Object", None), "Proxy", None)
        name = ICONS.get(getattr(object_type, "Type", ""), self.IconName)
        return icon(name)

    def claimChildren(self) -> list[Any]:
        return []
