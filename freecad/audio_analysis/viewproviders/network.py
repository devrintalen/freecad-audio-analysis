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
    "Audio::Crossover": "Crossover",
    "Audio::FrequencySweep": "Sweep",
    "Audio::SolverLumped": "Solve",
    "Audio::ParameterSweep": "Sweep",
    "Audio::TargetCurve": "Target",
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
        """Nest the elements filed under this node, so the tree reads as a topology.

        The analysis is the only group, so the candidates are its members; an object
        outside any analysis claims nothing.
        """
        from freecad.audio_analysis.objects.network_objects import tree_children

        obj = getattr(self, "Object", None)
        if obj is None:
            return []
        for parent in obj.InList:
            members = getattr(parent, "Group", None)
            if members and obj in members:
                return tree_children(obj, list(members))
        return []
