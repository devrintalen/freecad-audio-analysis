"""The analysis container -- one acoustic study inside a FreeCAD document.

Mirrors the FEM workbench's ``Fem::FemAnalysis``: a group holding everything that
describes one study (environment, domains, sources, boundaries, probes, solvers,
results). A document may hold several, so a user can keep "sealed" and "vented" variants
of the same enclosure side by side and compare their results.
"""

from __future__ import annotations

from typing import Any, Iterable

from freecad.audio_analysis.objects.base import AudioObject, PropertySpec, attach_view_provider


class AudioAnalysis(AudioObject):
    """Container object for one acoustic analysis."""

    Type = "Audio::Analysis"

    def properties(self) -> Iterable[PropertySpec]:
        return (
            PropertySpec(
                "App::PropertyString",
                "Description",
                "Analysis",
                "Free-text note describing what this study is for",
                default="",
            ),
            PropertySpec(
                "App::PropertyString",
                "WorkingDirectory",
                "Analysis",
                "Directory for solver case files; empty means use a temporary directory",
                default="",
            ),
        )

    def members_of_type(self, obj: Any, type_name: str) -> list[Any]:
        """Return this analysis's children whose proxy Type matches ``type_name``."""
        from freecad.audio_analysis.objects.base import is_audio_object

        return [child for child in obj.Group if is_audio_object(child, type_name)]


def make_analysis(doc: Any, name: str = "AudioAnalysis") -> Any:
    """Create an analysis container in ``doc`` and return it.

    A view provider is attached only when the GUI is running, so this is usable from
    headless scripts and tests.
    """
    obj = doc.addObject("App::DocumentObjectGroupPython", name)
    AudioAnalysis(obj)
    attach_view_provider(
        obj, "freecad.audio_analysis.viewproviders.analysis:ViewProviderAudioAnalysis"
    )
    return obj
