"""View providers for the caps and cavities folders.

Each folder borrows the icon of what it holds, so a glance at the tree says which is which
without reading the labels -- the labels are the one part of a folder the user is free to
change.
"""

from __future__ import annotations

from typing import Any

from freecad.audio_analysis.viewproviders.base import ViewProviderAudioObject


class ViewProviderAudioFolder(ViewProviderAudioObject):
    """A plain group, drawn with the icon of whatever it collects.

    ``claimChildren`` is spelled out rather than inherited. Attaching a Python view
    provider to a ``DocumentObjectGroupPython`` replaces the C++ group view provider that
    would otherwise nest the members, so without this the folder appears in the tree with
    its contents scattered beside it instead of inside it.
    """

    def claimChildren(self) -> list[Any]:
        obj = getattr(self, "Object", None)
        if obj is None:
            return []
        return list(getattr(obj, "Group", []) or [])


class ViewProviderCapFolder(ViewProviderAudioFolder):
    IconName = "Cap"


class ViewProviderCavityFolder(ViewProviderAudioFolder):
    IconName = "Cavity"
