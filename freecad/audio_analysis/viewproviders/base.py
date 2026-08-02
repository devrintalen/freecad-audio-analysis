"""Base view provider for Audio Analysis objects.

Everything in this package imports ``FreeCADGui`` and must therefore never be imported
from ``objects/`` or ``physics/`` at module level -- those layers stay headless-testable
(CLAUDE.md). Factories in ``objects/`` import view providers lazily, inside a try/except.
"""

from __future__ import annotations

from typing import Any

from freecad.audio_analysis import icon


class ViewProviderAudioObject:
    """Common view-provider behaviour: an icon, and no children of its own."""

    #: Icon basename in ``resources/icons``. Subclasses override.
    IconName = "AudioAnalysis"

    def __init__(self, vobj: Any) -> None:
        vobj.Proxy = self

    def attach(self, vobj: Any) -> None:
        self.ViewObject = vobj
        self.Object = vobj.Object

    def getIcon(self) -> str:
        return icon(self.IconName)

    def onDelete(self, vobj: Any, subelements: Any) -> bool:
        return True

    # FreeCAD 1.0+ serialisation hooks. View providers carry no state of their own.
    def dumps(self) -> None:
        return None

    def loads(self, state: Any) -> None:
        return None
